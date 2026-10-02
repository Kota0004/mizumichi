#!/usr/bin/env python3
"""レビュー済みデータを、アプリ（prototype）が読む形に書き出す

公開されるファイルなので、次を守る:
  ・除外した地点は出さない
  ・人手で未確認の地点は verified=false を付けて出す（確認済みと偽らない）
  ・位置が推定でしかない地点は precision="area" を付ける（地点として扱わせない）
  ・出典を必ず埋め込む

    python3 tools/export_prototype_data.py --in data/spots_chiba.json \\
        --out prototype/data/spots_chiba.json

複数県をまとめて1つにすることもできる:

    python3 tools/export_prototype_data.py --in data/spots_*.json \\
        --out prototype/data/spots.json --area-name 関東
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import risk  # noqa: E402

KEEP = ("id", "name", "kind", "lon", "lat", "dz", "hist", "road", "road_type", "address")

# 位置の確からしさ。アプリはこれを見て、地点アラートを鳴らすかどうかを決める。
#
#   point … 資料に番地までの住所があるか、名称が一致する構造物へ短距離で寄せた。
#           その地点に立っていると言ってよい。
#   area  … 資料の住所が市区町村までしかなく、座標は区の中心か、そこから
#           名称の部分一致だけで遠くへ寄せたもの。「この付近にある」以上のことは
#           言えないので、地点として警報を鳴らしてはいけない。
#
# 東京都の資料は「地先名又は通称名」が住所ではなく構造物名で、番地が無い。
# 区の中心から2.9km寄せた座標を地点として扱うと、本当に危ない場所で警報が鳴らず、
# 無関係な場所で鳴る。それは地点を持たないより悪い。
COARSE_CONF = 0.45
SHORT_SNAP_M = 300.0


def position_precision(spot: dict) -> tuple[str, str]:
    """この座標を「地点」と呼んでよいか。戻り値は (precision, 理由)。"""
    ev = spot.get("evidence") or {}
    snap = (spot.get("params") or {}).get("snap") or {}
    conf = ev.get("confidence")
    coarse_source = (isinstance(conf, (int, float)) and conf <= COARSE_CONF) \
        or "市区町村" in str(ev.get("note", ""))

    if ev.get("verified_at"):
        return "point", "人手で位置を確認済み"
    if not coarse_source:
        return "point", "資料に番地までの住所がある"
    moved = snap.get("moved_m")
    if moved is not None and moved <= SHORT_SNAP_M and snap.get("confidence") == "高":
        return "point", f"名称が一致する構造物へ {moved:.0f}m 寄せた"
    if moved is not None:
        return "area", (f"資料の住所が市区町村までで、そこから {moved:.0f}m 寄せた推定位置")
    return "area", "資料の住所が市区町村までしかない"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", required=True, nargs="+",
                    help="レビュー済みデータ。複数指定するとまとめて1つにする")
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--area-name", default="千葉県")
    args = ap.parse_args()

    out, skipped = [], 0
    sources: list[str] = []
    seen_ids: set[str] = set()
    for path in args.src:
        src = json.loads(Path(path).read_text(encoding="utf-8"))
        first_ev = (src.get("spots") or [{}])[0].get("evidence") or {}
        if first_ev.get("source"):
            sources.append(first_ev["source"])
        for s in src["spots"]:
            status = (s.get("review") or {}).get("status", "pending")
            if status == "excluded" or s.get("lon") is None:
                skipped += 1
                continue
            # 県をまたいでIDが衝突すると、片方が消えたり
            # 取り込み済み雨量の紐付けが狂ったりする。気づけるように止める。
            if s["id"] in seen_ids:
                print(f"IDが重複しています: {s['id']}", file=sys.stderr)
                return 2
            seen_ids.add(s["id"])
            ev = s.get("evidence") or {}
            rec = {k: s.get(k) for k in KEEP if s.get(k) is not None}
            rec["verified"] = bool(ev.get("verified_at"))
            rec["precision"], rec["precision_note"] = position_precision(s)
            rec["t60"] = round(risk.compute_t60(s), 1)
            snap = (s.get("params") or {}).get("snap")
            if snap:
                rec["snap"] = {"moved_m": snap.get("moved_m"),
                               "matched": snap.get("matched"),
                               "confidence": snap.get("confidence")}
            out.append(rec)

    verified = sum(1 for r in out if r["verified"])
    area_only = sum(1 for r in out if r["precision"] == "area")
    payload = {
        "version": "1.0.0",
        "generated_at": date.today().isoformat(),
        "area": args.area_name,
        "counts": {"total": len(out), "verified": verified,
                   "unverified": len(out) - verified,
                   "point": len(out) - area_only, "area": area_only},
        "attribution": [
            "危険箇所: " + (" ／ ".join(dict.fromkeys(sources))
                          or "国土交通省 道路冠水注意箇所マップ"),
            "位置の補正: © OpenStreetMap contributors (ODbL)",
            "地図・標高: 地理院タイル（国土地理院）",
        ],
        "disclaimer": (
            "参考情報です。通行の可否を保証するものではありません。"
            "最終判断は現地の状況で行ってください。"
            f"位置が人手で未確認の地点が {len(out) - verified} 件含まれます。"
            + (f"うち {area_only} 件は資料に番地が無く、位置は市区町村レベルの"
               "推定です（地点の警報は鳴らしません）。" if area_only else "")),
        "spots": out,
    }
    dst = Path(args.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"書き出し: {dst}")
    print(f"  {len(out)} 件（確認済み {verified} / 未確認 {len(out)-verified}）"
          f" ／ 除外・座標なしで省いた {skipped} 件")
    print(f"  位置: 地点として使える {len(out)-area_only} 件 / "
          f"市区町村レベルの推定 {area_only} 件")
    print(f"  閾値 T60: {min(r['t60'] for r in out):.0f}〜{max(r['t60'] for r in out):.0f} mm/h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
