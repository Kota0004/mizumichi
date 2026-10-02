#!/usr/bin/env python3
"""危険箇所データの点検（公開・配布の前に必ず通す）

座標の妥当性、重複、レビューの進み具合、閾値の分布、危険度の出方をまとめて調べる。
「誤った地点を危険と表示する」ことを防ぐための最後の関門（docs/07）。

    python3 tools/validate_spots.py --spots data/spots_chiba.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import geo  # noqa: E402
import risk  # noqa: E402

# 日本のおおよその範囲（南鳥島・与那国島まで含む）。
# ここから出ていれば、座標そのものが壊れている。
JAPAN_BBOX = (122.9, 20.4, 154.0, 45.6)

# 都道府県のおおよその範囲。分かっているものだけ書く。
# 47都道府県ぶんを記憶で書くと誤りが混ざり、正しい座標を誤検知してしまうので、
# 実際に確かめたものだけを載せ、無い県は下の「仲間はずれ探し」で見る。
BBOXES = {
    "chiba": (139.74, 34.87, 140.90, 36.11),
}

# 仲間はずれ探し: 同じ県の地点はまとまって分布するはずなので、
# 中央値から極端に離れた地点はジオコーディングの失敗を疑う。
OUTLIER_KM = 150.0
# トンネル・立体は、標高データが「上の地形」を測るため大きな高低差が出て当然。
# 座標のずれとは区別する。
TUNNELISH_RE = re.compile(r"(トンネル|隧道|立体)")
DUP_M = 30.0
IMPLAUSIBLE_DZ_M = 6.0
# この距離以内にトンネル等があれば、大きな高低差は構造由来と判断する
NEAR_STRUCTURE_M = 25.0

problems: list[str] = []
notes: list[str] = []


def load_osm(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("ways", [])
    except (json.JSONDecodeError, OSError):
        return []


def explains_dz(spot: dict, ways: list[dict]) -> str:
    """大きな高低差に構造上の理由があるか。

    標高データは地表を測る。トンネル・掘割・ガード下では道路面ではなく
    上の地形（山・築堤・跨線橋）の高さが出るので、大きな高低差は当然出る。
    「近くにその構造物が実在するか」をOSMで確かめて、座標の誤りと切り分ける。
    """
    if TUNNELISH_RE.search(spot.get("name", "")):
        return "名前がトンネル・立体"
    if not ways or spot.get("lon") is None:
        return ""
    hit = geo.nearest_on_ways(spot["lon"], spot["lat"], ways, max_m=NEAR_STRUCTURE_M)
    if not hit:
        return ""
    tags = hit["way"].get("tags") or {}
    name = tags.get("name") or tags.get("ref") or "名前なし"
    kind = ("道路トンネル" if tags.get("tunnel") not in (None, "no")
            else "掘割・地下" if str(tags.get("layer", "")).startswith("-")
            else "鉄道橋（ガード下）")
    return f"{hit['distance_m']:.0f}m先に{kind}（{name}）"


def head(t):
    print(f"\n── {t} " + "─" * max(0, 46 - len(t)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spots", required=True)
    ap.add_argument("--area", default="chiba")
    ap.add_argument("--rains", default="30,50,80,100", help="危険度を試算する時間雨量")
    ap.add_argument("--osm", default="data/osm_underpasses.json",
                    help="OSMのトンネル情報。あれば高低差の原因を切り分ける")
    ap.add_argument("--annotate", action="store_true",
                    help="見つかった疑いを spots に書き戻す（レビュー画面に表示される）")
    args = ap.parse_args()

    doc = json.loads(Path(args.spots).read_text(encoding="utf-8"))
    S = doc["spots"]
    live = [x for x in S if (x.get("review") or {}).get("status") != "excluded"]
    hints: dict[str, list[str]] = {}

    def hint(spot, text):
        hints.setdefault(spot["id"], []).append(text)

    head("レビューの進み具合")
    st = collections.Counter((x.get("review") or {}).get("status", "pending") for x in S)
    print(f"  全 {len(S)} 件: 確認済み {st['ok']} / 除外 {st['excluded']} / 未確定 {st['pending']}")
    if st["pending"]:
        pend = [x["id"] for x in S if (x.get("review") or {}).get("status", "pending") == "pending"]
        problems.append(f"未確定が {st['pending']} 件あります（{pend[0]}〜{pend[-1]}）。"
                        "未確定のまま公開・配布しないでください")
        print(f"  未確定: {pend[0]}〜{pend[-1]}")

    head("座標の妥当性")
    noxy = [x["id"] for x in live if x.get("lon") is None]
    if noxy:
        problems.append(f"座標が無い地点が {len(noxy)} 件: {noxy[:5]}")
    print(f"  座標なし: {len(noxy)} 件")

    # エリア名は --area が既定のままでも、地点IDの接頭辞から拾えるようにする。
    # 県を増やしたときに --area の指定漏れで誤検知するのを避ける。
    area = args.area
    ids = [x["id"] for x in live if "-" in x.get("id", "")]
    if ids:
        prefixes = {i.rsplit("-", 1)[0] for i in ids}
        if len(prefixes) == 1:
            area = prefixes.pop()

    xy = [x for x in live if x.get("lon") is not None]

    outside = [x["id"] for x in xy
               if not (JAPAN_BBOX[0] <= x["lon"] <= JAPAN_BBOX[2]
                       and JAPAN_BBOX[1] <= x["lat"] <= JAPAN_BBOX[3])]
    if outside:
        problems.append(f"日本の範囲外に出ている地点が {len(outside)} 件: {outside[:5]}")
    print(f"  日本の範囲外: {len(outside)} 件")

    bb = BBOXES.get(area)
    if bb:
        out = [x["id"] for x in xy
               if not (bb[0] <= x["lon"] <= bb[2] and bb[1] <= x["lat"] <= bb[3])]
        if out:
            problems.append(f"{area} の範囲外に出ている地点が {len(out)} 件: {out[:5]}")
        print(f"  {area} の範囲外: {len(out)} 件")
    else:
        print(f"  {area} の範囲は未登録（仲間はずれ探しで代用）")

    # 中央値から極端に離れた地点を探す。県の範囲を知らなくても、
    # 1件だけ遠くへ飛んだジオコーディング失敗は見つけられる。
    if len(xy) >= 5:
        import statistics
        mlon = statistics.median(x["lon"] for x in xy)
        mlat = statistics.median(x["lat"] for x in xy)
        far = [(x["id"], geo.haversine_m(x["lon"], x["lat"], mlon, mlat) / 1000.0)
               for x in xy]
        far = [(i, d) for i, d in far if d > OUTLIER_KM]
        if far:
            far.sort(key=lambda t: -t[1])
            problems.append(
                f"他の地点から極端に離れた地点が {len(far)} 件: "
                + ", ".join(f"{i}({d:.0f}km)" for i, d in far[:5]))
        print(f"  中央から {OUTLIER_KM:.0f}km 超: {len(far)} 件")

    dups = []
    pts = [x for x in live if x.get("lon") is not None]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            a, b = pts[i], pts[j]
            dd = geo.haversine_m(a["lon"], a["lat"], b["lon"], b["lat"])
            if dd < DUP_M:
                dups.append((a, b, dd))
    print(f"  {DUP_M:.0f}m以内に重なる組: {len(dups)} 件")
    for a, b, dd in dups:
        mark = "★同じ座標" if dd < 1 else ""
        print(f"    {a['id']} {a['name'][:18]:<20} ↔ {b['id']} {b['name'][:18]:<20} {dd:>4.0f}m {mark}")
        ra, rb = a.get("road", ""), b.get("road", "")
        if ra and rb and ra != rb:
            print(f"      路線が違う（{ra} / {rb}）。別の構造物のはずなので、"
                  "どちらかの位置が誤っています")
            hint(a, f"{b['name']}（路線 {rb}）と {dd:.0f}m しか離れていません。"
                    f"こちらは路線 {ra} なので別の構造物のはずです")
            hint(b, f"{a['name']}（路線 {ra}）と {dd:.0f}m しか離れていません。"
                    f"こちらは路線 {rb} なので別の構造物のはずです")
        else:
            hint(a, f"{b['name']} と {dd:.0f}m しか離れていません")
            hint(b, f"{a['name']} と {dd:.0f}m しか離れていません")
    if dups:
        problems.append(f"座標が重なる組が {len(dups)} 件あります。"
                        "別の構造物なら、どちらかの位置が誤っています")

    head("標高（dz）")
    dz = sorted(x.get("dz", 0) for x in live)
    if dz:
        print(f"  最小 {dz[0]:+.2f}m / 中央 {dz[len(dz)//2]:+.2f}m / 最大 {dz[-1]:+.2f}m")
        print(f"  窪地(<-1m) {sum(1 for v in dz if v < -1)} / "
              f"平坦(±1m) {sum(1 for v in dz if -1 <= v <= 1)} / "
              f"高い(>+1m) {sum(1 for v in dz if v > 1)} 件")
    ways = load_osm(args.osm)
    odd_real, odd_ok = [], []
    for x in live:
        if abs(x.get("dz", 0)) <= IMPLAUSIBLE_DZ_M:
            continue
        why = explains_dz(x, ways)
        (odd_ok if why else odd_real).append((x, why))
    if odd_ok:
        print(f"\n  高低差が大きいが説明がつくもの: {len(odd_ok)} 件")
        for x, why in odd_ok:
            print(f"    {x['id']} {x['name'][:22]:<24} dz={x['dz']:+6.2f}m  ← {why}")
        notes.append("トンネル・掘割・ガード下は、標高データが『上の地形』を測るため"
                     "大きな高低差が出ます。座標の誤りとは限りません")
    if odd_real:
        print(f"\n  高低差が不自然（座標のずれを疑う）: {len(odd_real)} 件")
        for x, _ in odd_real:
            print(f"    {x['id']} {x['name'][:22]:<24} dz={x['dz']:+6.2f}m"
                  + ("  ← 近くにトンネル等が無い" if ways else ""))
            hint(x, f"周囲との高低差が {x['dz']:+.1f}m と大きく、近くにトンネル等もありません。"
                    "座標がずれている可能性があります")
        problems.append(f"高低差が不自然な地点が {len(odd_real)} 件。座標を確認してください")

    head("種別の根拠")
    ks = collections.Counter(x.get("kind_source", "(記録なし)") for x in live)
    for k, v in ks.most_common():
        print(f"  {v:>3} 件  {k}")
    default_n = sum(v for k, v in ks.items() if "既定値" in k)
    if default_n:
        notes.append(f"種別が既定値のままの地点が {default_n} 件。"
                     "ポンプ設備の有無が分かれば閾値が 35→55mm/h に変わります（docs/08 の質問4）")

    head("冠水閾値 T60 の分布")
    rows = []
    for x in live:
        sp = risk.Spot(id=x["id"], name=x.get("name", ""), kind=x.get("kind", "lowland"),
                       lon=x.get("lon") or 0, lat=x.get("lat") or 0,
                       dz=x.get("dz", 0), hist=x.get("hist", 0))
        rows.append((sp.t60, x))
    rows.sort(key=lambda r: r[0])
    vals = [r[0] for r in rows]
    print(f"  最小 {vals[0]:.1f} / 中央 {vals[len(vals)//2]:.1f} / 最大 {vals[-1]:.1f} mm/h"
          f"（幅 {vals[-1]-vals[0]:.1f}）")
    print("  最も危険（閾値が低い）3件:")
    for v, x in rows[:3]:
        print(f"    {v:5.1f} mm/h  {x['id']} {x['name'][:22]}")
    print("  最も鈍い（閾値が高い）3件:")
    for v, x in rows[-3:]:
        print(f"    {v:5.1f} mm/h  {x['id']} {x['name'][:22]}")

    head("時間雨量ごとの危険度")
    print("        " + "".join(f"{'Lv'+str(k):>7}" for k in range(5)))
    flat = []
    for r60 in [float(v) for v in args.rains.split(",")]:
        c = collections.Counter()
        for _, x in rows:
            sp = risk.Spot(id=x["id"], name=x.get("name", ""), kind=x.get("kind", "lowland"),
                           lon=x.get("lon") or 0, lat=x.get("lat") or 0,
                           dz=x.get("dz", 0), hist=x.get("hist", 0))
            c[risk.score(sp, r60=r60)["level"]] += 1
        print(f"  {r60:>4.0f}mm " + "".join(f"{c[k]:>7}" for k in range(5)))
        top = max(c.values())
        if top == len(rows):
            flat.append(r60)
    if flat:
        problems.append(
            f"時間雨量 {'/'.join(f'{v:.0f}' for v in flat)}mm で、全地点が同じレベルになります。"
            "その雨量では『どこが特に危ないか』を伝えられません（docs/04 の較正が必要）")

    if args.annotate:
        for x in S:
            h = hints.get(x["id"])
            if h:
                x.setdefault("params", {})["check_hint"] = "／".join(h)
            elif (x.get("params") or {}).pop("check_hint", None) is not None:
                pass        # 解消したヒントは消す
        Path(args.spots).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print(f"\n{len(hints)} 件に確認のヒントを書き込みました → {args.spots}")

    print("\n" + "=" * 52)
    if problems:
        print(f"⚠ 対応が必要: {len(problems)} 件")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
    else:
        print("✅ 対応が必要な問題は見つかりませんでした")
    if notes:
        print(f"\n参考: {len(notes)} 件")
        for i, n in enumerate(notes, 1):
            print(f"  {i}. {n}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
