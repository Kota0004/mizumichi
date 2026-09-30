#!/usr/bin/env python3
"""取り込み直しで、人手のレビュー結果が壊れないことの検証

資料は年に数回更新されるので取り込み直すことになる。そのたびに
レビュー結果（確認済みの印、手で直した座標、除外の判断）が消えると、
同じ作業をやり直させることになる。ここが壊れたら気づけるよう固定しておく。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_spots as B  # noqa: E402
ok, ng = [], []


def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


def reviewed_spot(i, status="ok", verified="2026-09-01", lon=140.5, lat=35.5):
    """人手が触った地点。座標も手で直してある想定。"""
    return {
        "id": f"chiba-{i:04d}", "name": f"確認済み{i}", "kind": "underpass_pump",
        "lon": lon, "lat": lat, "dz": -2.5, "hist": 2,
        "road": "手で直した路線", "address": "千葉県確認市1丁目1番",
        "evidence": {"confidence": 1.0, "note": "", "verified_at": verified,
                     "source": "旧版"},
        "review": {"status": status},
    }


def fresh_spot(i):
    """取り込み直しで出てくる、素の抽出結果。"""
    return {
        "id": f"chiba-{i:04d}", "name": f"新規{i}", "kind": "underpass_gravity",
        "lon": 0.0, "lat": 0.0, "dz": 0.0, "hist": 0,
        "road": "新しい路線", "address": "千葉県新市",
        "evidence": {"confidence": 0.4, "note": "新規", "verified_at": None,
                     "source": "新版"},
    }


def merge(prev_spots, fresh_spots, overwrite=False):
    """build_spots.py の本物の関数を呼ぶ。

    ロジックを写して確かめると「写した側が正しいか」しか分からないので、
    実装そのものを通す。
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "spots.json"
        out.write_text(json.dumps({"spots": prev_spots}, ensure_ascii=False),
                       encoding="utf-8")
        spots, kept = B.preserve_reviewed([dict(s) for s in fresh_spots],
                                          out, overwrite)
        return {"spots": spots, "kept": kept}


prev = [reviewed_spot(1), reviewed_spot(2, status="excluded", verified=None),
        {**fresh_spot(3), "review": {"status": "pending"}}]
fresh = [fresh_spot(1), fresh_spot(2), fresh_spot(3)]
res = merge(prev, fresh)
by_id = {s["id"]: s for s in res["spots"]}

check("確認済みの地点は守られる", by_id["chiba-0001"]["name"] == "確認済み1",
      by_id["chiba-0001"]["name"])
check("手で直した座標が消えない", by_id["chiba-0001"]["lon"] == 140.5,
      str(by_id["chiba-0001"]["lon"]))
check("確認済みの印が消えない",
      by_id["chiba-0001"]["evidence"]["verified_at"] == "2026-09-01")
check("除外の判断も守られる（verified_at が無くても）",
      by_id["chiba-0002"].get("review", {}).get("status") == "excluded",
      str(by_id["chiba-0002"].get("review")))
check("未レビューの地点は新しい内容に入れ替わる",
      by_id["chiba-0003"]["name"] == "新規3", by_id["chiba-0003"]["name"])
check("守った件数を数える", res["kept"] == 2, f"{res['kept']} 件")

# 明示的に指定したときだけ上書きできる
res2 = merge(prev, fresh, overwrite=True)
by_id2 = {s["id"]: s for s in res2["spots"]}
check("--overwrite-reviewed なら上書きする",
      by_id2["chiba-0001"]["name"] == "新規1" and res2["kept"] == 0)

# 資料から消えた地点は、守らずに消える（存在しない箇所を残さない）
res3 = merge(prev, [fresh_spot(1)])
check("資料から消えた地点は残さない", len(res3["spots"]) == 1,
      f"{len(res3['spots'])} 件")

# 既存ファイルが壊れていても取り込みは止まらない
res4 = merge([], fresh)
check("既存に確認済みが無ければそのまま", res4["kept"] == 0 and len(res4["spots"]) == 3)

# 既存ファイルが壊れていても、取り込み自体は止めない。
# ここで落とすと、1回JSONが壊れただけで取り込みができなくなる。
with tempfile.TemporaryDirectory() as td:
    broken = Path(td) / "spots.json"
    broken.write_text("{壊れている", encoding="utf-8")
    got, kept = B.preserve_reviewed([fresh_spot(1)], broken)
    check("既存ファイルが壊れていても止まらない", len(got) == 1 and kept == 0)

# 出力ファイルがまだ無い初回も素通りする
with tempfile.TemporaryDirectory() as td:
    got, kept = B.preserve_reviewed([fresh_spot(1)], Path(td) / "none.json")
    check("初回（出力が無い）は素通りする", len(got) == 1 and kept == 0)

check("was_reviewed: pending は触っていない扱い",
      not B.was_reviewed({"review": {"status": "pending"}, "evidence": {}}))
check("was_reviewed: ok は触った扱い",
      B.was_reviewed({"review": {"status": "ok"}, "evidence": {}}))
check("was_reviewed: 確認日だけでも触った扱い",
      B.was_reviewed({"evidence": {"verified_at": "2026-09-01"}}))

print("\n===== 取り込み直しでレビューが壊れないか =====")
for s in ok:
    print("  OK " + s)
for s in ng:
    print("  NG " + s)
print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
raise SystemExit(1 if ng else 0)
