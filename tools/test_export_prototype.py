#!/usr/bin/env python3
"""export_prototype_data.py の検証（ネットワーク不要）

公開されるファイルを作るツールなので、次を確かめる:
  ・除外した地点を出さない
  ・位置が推定でしかない地点に precision="area" を付ける
  ・複数県をまとめても取りこぼさない／IDが衝突したら止まる
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ok, ng = [], []


def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


def spot(i, **over):
    s = {
        "id": f"chiba-{i:04d}", "name": f"地点{i}", "kind": "underpass_gravity",
        "lon": 140.10 + i * 0.001, "lat": 35.60 + i * 0.001, "dz": -1.0, "hist": 0,
        "road": "市道1号", "address": f"千葉県試験市{i}丁目1番",
        "evidence": {"confidence": 0.9, "note": "", "verified_at": "2026-09-01",
                     "source": "国交省千葉国道事務所"},
        "review": {"status": "ok"},
    }
    s.update(over)
    return s


def run(files, extra=None):
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for i, data in enumerate(files):
            p = Path(td) / f"in{i}.json"
            p.write_text(json.dumps({"spots": data}, ensure_ascii=False), encoding="utf-8")
            paths.append(str(p))
        out = Path(td) / "out.json"
        r = subprocess.run(
            [sys.executable, str(HERE / "export_prototype_data.py"),
             "--in", *paths, "--out", str(out)] + (extra or []),
            capture_output=True, text=True, cwd=ROOT)
        payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return r, payload


# 除外した地点は出さない
r, pay = run([[spot(1), spot(2, review={"status": "excluded"})]])
check("除外した地点は出さない", pay and len(pay["spots"]) == 1,
      f"{pay and len(pay['spots'])} 件")

# 座標が無い地点も出さない（地図に置けないため）
r, pay = run([[spot(1), spot(2, lon=None, lat=None)]])
check("座標が無い地点は出さない", pay and len(pay["spots"]) == 1)

# 番地のある住所は地点として扱う
r, pay = run([[spot(1)]])
check("番地のある住所は point", pay and pay["spots"][0]["precision"] == "point",
      pay and pay["spots"][0].get("precision_note"))

# 市区町村どまりは推定として扱う。ここが point になると、
# 区の中心で現在地アラートが鳴ってしまう。
coarse = spot(3, evidence={"confidence": 0.3, "note": "市区町村レベルまでしか特定できていない",
                           "verified_at": None, "source": "国交省 東京国道事務所"},
              review={"status": "pending"})
r, pay = run([[coarse]])
check("市区町村どまりは area", pay and pay["spots"][0]["precision"] == "area",
      pay and pay["spots"][0].get("precision_note"))
check("推定である理由を書く", pay and "市区町村" in pay["spots"][0]["precision_note"])

# 人手で確認済みなら、資料が粗くても地点として扱ってよい
verified_coarse = copy.deepcopy(coarse)
verified_coarse["evidence"]["verified_at"] = "2026-09-01"
r, pay = run([[verified_coarse]])
check("人手で確認済みなら point", pay and pay["spots"][0]["precision"] == "point")

# 名称一致で短距離なら地点として扱う
snapped = copy.deepcopy(coarse)
snapped["params"] = {"snap": {"moved_m": 40, "confidence": "高", "matched": "道路トンネル"}}
r, pay = run([[snapped]])
check("名称一致で短距離に寄せたら point", pay and pay["spots"][0]["precision"] == "point")

# 遠くへ寄せたものは推定のまま。2.9km動かした座標を地点と呼ばないこと。
far = copy.deepcopy(coarse)
far["params"] = {"snap": {"moved_m": 2879, "confidence": "中", "matched": "道路トンネル"}}
r, pay = run([[far]])
check("遠くへ寄せたものは area のまま", pay and pay["spots"][0]["precision"] == "area",
      pay and pay["spots"][0].get("precision_note"))

# 複数県をまとめる
tokyo = [dict(coarse, id=f"tokyo-{i:04d}") for i in (1, 2)]
r, pay = run([[spot(1), spot(2)], tokyo])
check("複数県をまとめて出せる", pay and len(pay["spots"]) == 4, f"{pay and len(pay['spots'])} 件")
check("まとめても内訳を数える",
      pay and pay["counts"]["point"] == 2 and pay["counts"]["area"] == 2,
      str(pay and pay["counts"]))
check("出典を県ぶんつなぐ",
      pay and "千葉国道事務所" in pay["attribution"][0]
      and "東京国道事務所" in pay["attribution"][0],
      pay and pay["attribution"][0])
check("推定が含まれることを免責に書く",
      pay and "市区町村レベルの推定" in pay["disclaimer"])

# IDが衝突したら止まる。黙って片方を落とすと、
# 取り込み済み雨量の紐付けが狂ったまま公開されてしまう。
r, pay = run([[spot(1)], [spot(1)]])
check("IDが衝突したら止まる", r.returncode != 0, f"終了コード {r.returncode}")
check("どのIDが衝突したかを言う", "chiba-0001" in r.stderr, r.stderr.strip()[-60:])

print("\n===== export_prototype_data の検証 =====")
for s in ok:
    print("  OK " + s)
for s in ng:
    print("  NG " + s)
print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
raise SystemExit(1 if ng else 0)
