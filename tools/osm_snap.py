#!/usr/bin/env python3
"""OpenStreetMap のトンネル情報を使って、危険箇所の座標を実際の構造物へ寄せる

住所からのジオコーディングは「町丁目の代表点」を返すため、アンダーパスや
ガード下そのものは指さない（実データでも、住所は合っているのに位置が
数十〜数百mずれる、という状態になった）。

OSM には日本のアンダーパスが tunnel / layer=-1 として登録されているので、
各地点の近くにあるトンネル区間へ寄せることで、レビューの出発点を良くする。

⚠️ 自動で寄せた位置も**確定ではない**。必ず review_spots.py で目視確認すること。
   寄せた根拠と移動距離は params.snap に記録される。

使い方:
    # 1) 対象範囲のトンネル情報をまとめて取得（Overpass API に1回だけ問い合わせ）
    python3 tools/osm_snap.py --spots data/spots_chiba.json --fetch

    # 2) 寄せてみる（まず確認だけ）
    python3 tools/osm_snap.py --spots data/spots_chiba.json --dry-run

    # 3) 反映する
    python3 tools/osm_snap.py --spots data/spots_chiba.json --apply

データ出典: © OpenStreetMap contributors（ODbL）。利用時は出典表示が必要。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import geo  # noqa: E402

OVERPASS = "https://overpass-api.de/api/interpreter"

# HTTPヘッダは latin-1 でしか送れないため、日本語を入れてはいけない。
# （入れると UnicodeEncodeError になり、通信する前に失敗する）
USER_AGENT = "mizumichi-osm-snap/0.1 (disaster-prevention research; +https://github.com/Kota0004/mizumichi)"

# 寄せ先の候補。上にあるものほど信頼できる。
# ガード下は「道路側にタグが無く、跨いでいる鉄道側が bridge」という登録も多いので、
# 見つからないときの手がかりとして鉄道橋も拾う。
CATEGORIES = [
    ("道路トンネル", 'way["highway"]["tunnel"]["tunnel"!~"^no$"]'),
    ("掘割・地下（layer<0）", 'way["highway"]["layer"~"^-[0-9]"]'),
    ("鉄道橋（ガード下の手がかり）", 'way["railway"]["bridge"]["bridge"!~"^no$"]'),
]


def build_query(bbox: tuple[float, float, float, float], timeout: int = 180) -> str:
    """bbox = (lat_min, lon_min, lat_max, lon_max)"""
    b = f"({bbox[0]:.5f},{bbox[1]:.5f},{bbox[2]:.5f},{bbox[3]:.5f})"
    parts = "\n  ".join(f"{sel}{b};" for _, sel in CATEGORIES)
    return f"[out:json][timeout:{timeout}];\n(\n  {parts}\n);\nout geom;"


def spots_bbox(spots: list[dict], margin_deg: float = 0.01,
               coarse_max_move: float = 0.0) -> tuple[float, float, float, float]:
    """取得範囲。出発点が粗い地点があれば、寄せ先が入るまで広げる。

    区の中心から最大3km寄せる可能性があるのに、取得範囲が1kmしかないと
    寄せ先がそもそも取れず、黙って「寄せられなかった」になってしまう。
    """
    lons = [s["lon"] for s in spots if s.get("lon") is not None]
    lats = [s["lat"] for s in spots if s.get("lat") is not None]
    if not lons:
        raise SystemExit("座標のある地点がありません。先に build_spots.py を実行してください。")
    if coarse_max_move and any(is_coarse(s) for s in spots):
        # 緯度1度 ≒ 111km。経度は緯度が上がるほど詰まるが、
        # 範囲は広めに取る方が安全なので緯度基準でそろえる。
        margin_deg = max(margin_deg, coarse_max_move / 111_000.0)
    return (min(lats) - margin_deg, min(lons) - margin_deg,
            max(lats) + margin_deg, max(lons) + margin_deg)


def fetch(bbox, cache: Path, retries: int = 2) -> dict:
    import requests

    query = build_query(bbox)
    print("Overpass API に問い合わせます（1回だけ・数十秒かかることがあります）")
    print(f"  範囲: 緯度 {bbox[0]:.3f}〜{bbox[2]:.3f} / 経度 {bbox[1]:.3f}〜{bbox[3]:.3f}")
    last = None
    for attempt in range(1, retries + 2):
        try:
            r = requests.post(OVERPASS, data={"data": query}, timeout=240,
                              headers={"User-Agent": USER_AGENT})
            if r.status_code in (429, 504):
                raise requests.exceptions.RetryError(f"混雑しています (HTTP {r.status_code})")
            r.raise_for_status()
            data = r.json()
            break
        except requests.exceptions.RequestException as e:
            # 通信まわりの失敗だけ再試行する。
            # 設定ミスなど、待っても直らないものを再試行すると時間を無駄にするだけ。
            last = e
            if attempt <= retries:
                wait = 20 * attempt
                print(f"  失敗（{e}）。{wait}秒待って再試行します…")
                time.sleep(wait)
            else:
                raise SystemExit(f"Overpass API から取得できませんでした: {last}")
        except Exception as e:                              # noqa: BLE001
            raise SystemExit(
                f"取得に失敗しました（再試行しても直らない種類の失敗です）: "
                f"{type(e).__name__}: {e}")

    ways = [e for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": date.today().isoformat(),
                                 "bbox": bbox, "ways": ways},
                                ensure_ascii=False), encoding="utf-8")
    print(f"  取得: {len(ways)} 本の線 → {cache}")
    return {"ways": ways}


def categorize(tags: dict) -> tuple[int, str]:
    """寄せ先の種類と優先度（小さいほど信頼できる）"""
    if tags.get("highway") and tags.get("tunnel") not in (None, "no"):
        return 0, "道路トンネル"
    if tags.get("highway") and str(tags.get("layer", "")).startswith("-"):
        return 1, "掘割・地下（layer<0）"
    if tags.get("railway") and tags.get("bridge") not in (None, "no"):
        return 2, "鉄道橋（ガード下の手がかり）"
    return 3, "その他"


# 種類ごとのペナルティ[m]。道路トンネルを優先しつつ、
# すぐ近くに鉄道橋（ガード下）があればそちらを選べるようにする。
PRIO_PENALTY_M = 60.0

# 路線名・通称が一致したときの「割引」[m]。名前が合う線は多少遠くても本命に近い。
REF_BONUS_M = 150.0     # 路線番号の一致（国道356号 ↔ ref=356）
NAME_BONUS_M = 120.0    # 名称の一致（新港穴川線 ↔ name に「新港穴川」）

# 出発点が市区町村の中心しかない地点は、上の上限では届かない。
# 東京都のPDFは「地先名又は通称名」が住所ではなく構造物名（「本町アンダー」など）で、
# ジオコーディングが区の中心に落ちるため、本来の位置まで数kmあることがある。
# 区の中心は位置として意味を持たないので、名称が一致する候補に限り遠くまで許す。
COARSE_MAX_MOVE_M = 3000.0
# この確からしさ以下なら「出発点が粗い」とみなす（build_spots の confidence）
COARSE_CONF = 0.45

REF_RE = re.compile(r"(?:国道|県道|市道|町道|村道|主要地方道)?\s*(\d{1,4})\s*号")

# 路線名（鉄道・道路）の抽出と正規化。
# 「JR総武線」と「総武本線」は同じ路線なので、同じものとして扱えるようにする。
LINE_RE = re.compile(r"([ぁ-んァ-ヶ一-龥A-Za-z]{2,10}(?:本線|新線|線))")
LINE_PREFIX_RE = re.compile(r"^(JR東日本|JR|東日本旅客鉄道|旅客鉄道)")
LINE_SUFFIX_RE = re.compile(r"(本線|新線|線)$")
# 路線名が食い違ったときの減点[m]。ガード下の地点で別の鉄道を掴むのは明確な誤り
LINE_CONFLICT_PENALTY_M = 250.0


def line_cores(text: str) -> set[str]:
    """文中の路線名を、比較できる形に揃えて取り出す。

    「JR総武線」「総武本線」→ どちらも「総武」
    「東武野田線」→「東武野田」、「野田線」→「野田」（部分一致で同一とみなす）
    """
    out = set()
    for raw in LINE_RE.findall(norm_text(text)):
        core = LINE_SUFFIX_RE.sub("", LINE_PREFIX_RE.sub("", raw)).strip()
        if len(core) >= 2:
            out.add(core)
    return out


def lines_agree(a: set[str], b: set[str]) -> bool:
    """路線名の集合が同じ路線を指しているか（部分一致を許す）"""
    if a & b:
        return True
    return any(x in y or y in x for x in a for y in b)
# 路線名から特徴のある部分を取り出す（「新港穴川線」→「新港穴川」）
NAME_TAIL_RE = re.compile(r"(線|号線|号|バイパス|通り|街道)$")


# 住所の表記。路線名ではないので、名前の手がかりにしない
ADDRESSY_RE = re.compile(r"(丁目|番地|地先|大字|小字|字[ぁ-ん一-龥])")
DIGITS_ONLY_RE = re.compile(r"^[\d\-‐−ー]+$")


def name_tokens(road: str = "", name: str = "", road_type: str = "") -> tuple[set[str], set[str]]:
    """(路線番号の集合, 名称の手がかりの集合) を返す。

    OSM が `ref` を持つのは国道・県道（主要地方道）が中心で、市道・町道の
    整理番号（「市道00-002号線」の 002 など）は入っていない。
    これを番号として扱うと、無関係な線に一致してしまうので、
    **国道・県道と分かる場合だけ**番号を取り出す。
    """
    refs, names = set(), set()
    ctx = norm_text(road_type) + norm_text(road)
    is_numbered_route = any(k in ctx for k in ("国道", "県道", "主要地方道"))

    for t in (road, name):
        t = norm_text(t)
        if not t:
            continue
        if is_numbered_route or re.match(r"^\d{1,4}\s*号", t):
            refs.update(REF_RE.findall(t))
        if ADDRESSY_RE.search(t):
            continue                       # 「袖ケ浦1丁目11番地先」は路線名ではない
        core = NAME_TAIL_RE.sub("", t)
        # 「アンダーパス」「地下道」などの一般語は、どの地点にもあるので手がかりにならない
        for generic in ("アンダーパス", "アンダー", "地下道", "ガード下", "ガード",
                        "立体交差", "立体", "橋詰", "国道", "県道", "市道", "町道", "村道"):
            core = core.replace(generic, "")
        core = core.strip()
        if len(core) >= 2 and not DIGITS_ONLY_RE.match(core):
            names.add(core)
    return refs, names


def norm_text(v) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", str(v or "")).strip()


def name_bonus(spot_refs: set[str], spot_names: set[str], tags: dict,
               spot_lines: set[str] | None = None) -> tuple[float, str]:
    """OSMの線の名前と地点の路線名を突き合わせ、割引または減点する。

    戻り値の1つ目は「効いた距離の調整量」（正なら割引、負なら減点）。
    """
    way_ref = norm_text(tags.get("ref"))
    way_name = norm_text(tags.get("name"))

    if spot_refs and way_ref:
        parts = {p.strip() for p in re.split(r"[;,/]", way_ref)}
        if spot_refs & parts:
            return REF_BONUS_M, f"路線番号が一致（{way_ref}）"

    # 資料に路線名があり、候補も路線名を持っていて、両者が別路線なら減点する。
    # 「JR常磐線中原ガード」を東武野田線に寄せるような取り違えを防ぐ。
    if spot_lines:
        way_lines = line_cores(way_name)
        if way_lines:
            if lines_agree(spot_lines, way_lines):
                return NAME_BONUS_M, f"路線名が一致（{way_name}）"
            return -LINE_CONFLICT_PENALTY_M, f"資料は{'・'.join(sorted(spot_lines))}線"

    if spot_names and way_name:
        for token in spot_names:
            if token in way_name or way_name in token:
                return NAME_BONUS_M, f"名称が一致（{way_name}）"
    return 0.0, ""


def is_coarse(spot: dict) -> bool:
    """出発点が市区町村・町名どまりで、位置として当てにならないか。

    ここが真なら、名称が一致する候補に限って遠くまで寄せることを許す。
    区の中心から動かさないでおくより、名前の合う構造物へ寄せた方が近い。
    """
    ev = spot.get("evidence") or {}
    conf = ev.get("confidence")
    if isinstance(conf, (int, float)) and conf <= COARSE_CONF:
        return True
    return "市区町村" in str(ev.get("note", ""))


def snap_one(spot: dict, ways: list[dict], max_move: float,
             max_move_unnamed: float | None = None,
             coarse_max_move: float | None = None) -> dict | None:
    """最も「もっともらしい」線へ寄せる。

    単純な最短距離ではなく、種類のペナルティを足した距離で比べる。
    そうしないと、たまたま近くを通る鉄道橋が、本命の道路トンネルより
    優先されてしまう（逆に、真上に鉄道橋があるガード下は拾えなくなる）。

    上限は2段階にする。実データでは、名前が一致しないのに200m以上動く候補は
    ほぼ別の構造物だった（「国道356号バイパス」が名前のないトンネルに294m など）。
    誤った位置に自信を持って置くより、動かさずに手で確認してもらう方が安全なので、
    **名前が一致しない候補には短い上限**をかける。
    """
    spot_refs, spot_names = name_tokens(spot.get("road", ""), spot.get("name", ""),
                                        spot.get("road_type", ""))
    spot_lines = line_cores(spot.get("name", "")) | line_cores(spot.get("locality", ""))
    if max_move_unnamed is None:
        max_move_unnamed = max_move
    # 出発点が粗いときだけ、名称が一致する候補への上限を広げる
    named_limit = max_move
    coarse = is_coarse(spot)
    if coarse and coarse_max_move:
        named_limit = max(max_move, coarse_max_move)

    # 出発点が粗いときは、名前の合う候補が1つに絞れたときだけ寄せる。
    # 「本町アンダー」のような通称名は部分一致しやすく、候補が複数あるときに
    # そのうちの1つを選ぶ根拠が無い。区の中心から2.9km動かすなら、
    # それが唯一の候補であることが最低条件。
    if coarse:
        matched_ids = set()
        for w in ways:
            tags = w.get("tags") or {}
            b, _ = name_bonus(spot_refs, spot_names, tags, spot_lines)
            if b > 0 and geo.nearest_on_ways(spot["lon"], spot["lat"], [w],
                                             max_m=named_limit):
                matched_ids.add(w.get("id"))
        if len(matched_ids) != 1:
            return None

    best = None
    for w in ways:
        tags = w.get("tags") or {}
        prio, label = categorize(tags)
        bonus, bonus_reason = name_bonus(spot_refs, spot_names, tags, spot_lines)
        # 出発点が市区町村の中心しかない地点は、名前が一致しない候補へは寄せない。
        #
        # 区の中心からの「31m」には意味がない。実際、東京都の北町・赤塚・徳丸の
        # 3つのアンダーパスは同じ区の中心から出発し、名前が合わないまま同じ
        # トンネルへ31m寄って、別々の危険箇所が1点に潰れた。
        # もっともらしい座標を作ってしまう分、寄せない方がまだ安全。
        if coarse and bonus <= 0:
            continue
        # 一致したものだけ遠くまで許す。減点されたものは通常の上限のまま
        limit = named_limit if bonus > 0 else min(max_move, max_move_unnamed)
        hit = geo.nearest_on_ways(spot["lon"], spot["lat"], [w], max_m=limit)
        if not hit:
            continue
        effective = hit["distance_m"] + prio * PRIO_PENALTY_M - bonus
        if best is None or effective < best["effective"]:
            best = {
                "effective": effective,
                "conflict": bonus < 0,
                "lon": round(hit["lon"], 6),
                "lat": round(hit["lat"], 6),
                "distance_m": round(hit["distance_m"], 1),
                "matched": label,
                "osm_id": w.get("id"),
                "osm_name": tags.get("name") or tags.get("ref") or "",
                "name_match": bonus_reason if bonus > 0 else "",
                "conflict_note": bonus_reason if bonus < 0 else "",
            }
    if best:
        best.pop("effective")
        best["from_coarse"] = coarse and best["distance_m"] > max_move
        best["snap_confidence"], best["why"] = snap_confidence(best)
    return best


def is_conflict(res: dict | None) -> bool:
    """資料の路線と違う線を掴んでいるか。掴んでいる場合は座標を動かさない。"""
    return bool(res and res.get("conflict_note"))


def snap_confidence(res: dict) -> tuple[str, str]:
    """寄せた結果の確からしさ。レビューでどこを重点的に見るかの目安にする。"""
    if res.get("conflict_note"):
        return "低", (f"{res['conflict_note']}なのに、別の路線（{res['osm_name']}）"
                      "しか近くにありません")
    if res["name_match"]:
        # 出発点が市区町村の中心しかなかった場合、名前が合っていても
        # 同名の別構造物を掴んでいる可能性が残る。断定せず目視に回す。
        if res.get("from_coarse"):
            return "中", (f"{res['name_match']}。ただし出発点が市区町村の中心で、"
                          f"そこから {res['distance_m']:.0f}m 寄せています。"
                          "同名の別の構造物でないか確認してください")
        if res["distance_m"] > 200:
            return "高", (f"{res['name_match']}。ただし {res['distance_m']:.0f}m 動いたので"
                          "元の座標がかなりずれていた可能性があります")
        return "高", res["name_match"]
    if res["distance_m"] <= 80:
        return "中", f"名前は一致しないが近い（{res['distance_m']:.0f}m）"
    return "低", (f"名前が一致せず {res['distance_m']:.0f}m 離れている。"
                  "別の構造物を掴んでいる可能性があります")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spots", required=True)
    ap.add_argument("--cache", default="data/osm_underpasses.json")
    ap.add_argument("--fetch", action="store_true", help="OSMからトンネル情報を取得する")
    ap.add_argument("--apply", action="store_true", help="寄せた座標を書き込む")
    ap.add_argument("--dry-run", action="store_true", help="寄せる内容を表示するだけ")
    ap.add_argument("--max-move", type=float, default=300.0,
                    help="路線名が一致した線への上限[m]（既定300）")
    ap.add_argument("--max-move-unnamed", type=float, default=150.0,
                    help="路線名が一致しない線への上限[m]（既定150）。"
                         "実データでは、名前が合わないのに200m以上動く候補は"
                         "ほぼ別の構造物だった")
    ap.add_argument("--coarse-max-move", type=float, default=COARSE_MAX_MOVE_M,
                    help=f"出発点が市区町村の中心しかない地点で、名称が一致する線への"
                         f"上限[m]（既定{COARSE_MAX_MOVE_M:.0f}）。0で無効")
    ap.add_argument("--include-reviewed", action="store_true",
                    help="人手で確認済みの地点も動かす（既定は動かさない）")
    args = ap.parse_args()

    spots_path = Path(args.spots)
    data = json.loads(spots_path.read_text(encoding="utf-8"))
    spots = data["spots"]
    cache = Path(args.cache)

    if args.fetch:
        fetch(spots_bbox(spots, coarse_max_move=args.coarse_max_move), cache)
        if not (args.apply or args.dry_run):
            print("\n次: python3 tools/osm_snap.py --spots "
                  f"{args.spots} --dry-run  で寄せる内容を確認してください")
            return 0

    if not cache.exists():
        print(f"OSMデータがありません: {cache}\n  先に --fetch を実行してください。", file=sys.stderr)
        return 1
    ways = json.loads(cache.read_text(encoding="utf-8"))["ways"]
    print(f"OSMの線: {len(ways)} 本")
    print(f"寄せる上限: 路線名が一致 {args.max_move:.0f}m / 一致しない {args.max_move_unnamed:.0f}m")

    moved, skipped_reviewed, not_found, conflicted = [], 0, [], []
    for s in spots:
        if s.get("lon") is None:
            continue
        if not args.include_reviewed and (s.get("evidence") or {}).get("verified_at"):
            skipped_reviewed += 1
            continue

        res = snap_one(s, ways, args.max_move, args.max_move_unnamed,
                       args.coarse_max_move)

        if is_conflict(res):
            # 資料と違う路線に寄せるくらいなら動かさない。
            # 丁目の中心に残っている方が、誤った構造物の上に置かれるよりまし。
            # ただし「近くに何があったか」は探す手がかりになるので残す。
            conflicted.append((s, res))
            if args.apply:
                s.setdefault("params", {})["snap_hint"] = {
                    "not_applied": True,
                    "nearby": f"{res['matched']} {res['osm_name']}".strip(),
                    "distance_m": res["distance_m"],
                    "why": res["why"],
                    "source": "© OpenStreetMap contributors (ODbL)",
                }
                s.setdefault("evidence", {})["note"] = (
                    f"{res['why']}。座標は動かしていません — "
                    f"地図で{res['conflict_note'].replace('資料は', '')}を探して合わせてください")
            continue

        if not res:
            not_found.append(s["id"])
            continue

        moved.append((s, res))
        if args.apply:
            s["lon"], s["lat"] = res["lon"], res["lat"]
            s.setdefault("params", {})["snap"] = {
                "moved_m": res["distance_m"],
                "matched": res["matched"],
                "osm_id": res["osm_id"],
                "osm_name": res["osm_name"],
                "confidence": res["snap_confidence"],
                "why": res["why"],
                "applied_at": date.today().isoformat(),
                "source": "© OpenStreetMap contributors (ODbL)",
            }
            s.setdefault("evidence", {})["note"] = (
                f"OSMの{res['matched']}へ {res['distance_m']:.0f}m 寄せた位置"
                f"（確からしさ {res['snap_confidence']}）。目視で確認してください")

    print(f"\n寄せられた地点: {len(moved)} 件 / 近くに見つからない: {len(not_found)} 件"
          + (f" / 路線が食い違うため動かさない: {len(conflicted)} 件" if conflicted else "")
          + (f" / 確認済みのため対象外: {skipped_reviewed} 件" if skipped_reviewed else ""))

    if conflicted:
        print("\n  資料と違う路線しか見つからなかった地点（動かしません）:")
        for s, r in conflicted:
            print(f"    {s['id']} {s.get('name','')[:22]:<24} 近くに {r['osm_name']}"
                  f"（{r['distance_m']:.0f}m）／ {r['conflict_note']}")
        print("    → 地図で資料の路線を探して、手で位置を合わせてください")

    if moved:
        dists = sorted(r["distance_m"] for _, r in moved)
        print(f"  移動距離: 中央値 {dists[len(dists)//2]:.0f}m / 最大 {dists[-1]:.0f}m")
        by_cat: dict[str, int] = {}
        for _, r in moved:
            by_cat[r["matched"]] = by_cat.get(r["matched"], 0) + 1
        for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            print(f"  {k}: {v} 件")

        by_conf: dict[str, int] = {}
        for _, r in moved:
            by_conf[r["snap_confidence"]] = by_conf.get(r["snap_confidence"], 0) + 1
        print("\n  寄せた結果の確からしさ:")
        for k in ("高", "中", "低"):
            if by_conf.get(k):
                print(f"    {k}: {by_conf[k]} 件"
                      + {"高": "（路線名が一致。ほぼ確実）",
                         "中": "（近いが名前は不明。要確認）",
                         "低": "（遠く名前も不一致。重点的に確認）"}[k])

        good = [(s, r) for s, r in moved if r["name_match"]]
        if good:
            print("\n  路線名が一致した例:")
            for s, r in good[:5]:
                print(f"    {s['id']} {s.get('name','')[:18]:<18} {r['distance_m']:>4.0f}m "
                      f"→ {r['osm_name']}  {r['why']}")
        weak = [(s, r) for s, r in moved if r["snap_confidence"] == "低"]
        if weak:
            print("\n  重点確認（確からしさ 低）の例:")
            for s, r in sorted(weak, key=lambda x: -x[1]["distance_m"])[:5]:
                print(f"    {s['id']} {s.get('name','')[:18]:<18} {r['distance_m']:>4.0f}m "
                      f"→ {r['matched']} {r['osm_name']}")
    if not_found:
        print(f"  近くにトンネルが見つからない: {not_found[:10]}"
              + (" ほか" if len(not_found) > 10 else ""))
        print("    → これらは手で位置を確認してください（OSMに未登録の可能性）")

    if args.dry_run or not args.apply:
        print("\n(--dry-run のため書き込んでいません。反映するには --apply)")
        return 0

    bak = spots_path.with_suffix(spots_path.suffix + ".pre-snap.bak")
    if not bak.exists():
        bak.write_text(json.dumps(json.loads(spots_path.read_text(encoding="utf-8")),
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nバックアップ: {bak}")
    data.setdefault("attribution", []).append("© OpenStreetMap contributors (ODbL)")
    data["attribution"] = sorted(set(data["attribution"]))
    spots_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"書き込み: {spots_path}")
    print("\n次: python3 tools/review_spots.py --spots "
          f"{args.spots} --ipad  で目視確認してください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
