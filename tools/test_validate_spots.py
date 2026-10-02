#!/usr/bin/env python3
"""validate_spots.py の検証（ネットワーク不要）

座標の妥当性検査が、実際に壊れた座標を捕まえられるかを確かめる。
検査そのものが素通りしていては、点検している気になるだけなので、
「壊した data を渡したら必ず気づく」ことを固定しておく。
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


def run(spots: dict) -> str:
    """spots を一時ファイルに書いて validate_spots.py を通し、出力を返す。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "spots.json"
        p.write_text(json.dumps(spots, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HERE / "validate_spots.py"), "--spots", str(p)],
            capture_output=True, text=True, cwd=ROOT)
        return r.stdout + r.stderr


def make_spots(n: int, area: str = "chiba") -> dict:
    """千葉あたりに固まった、素性の正しい地点をn件作る。"""
    return {
        "version": "1.0.0",
        "area": "千葉県",
        "spots": [
            {
                "id": f"{area}-{i:04d}",
                "name": f"試験地点{i}",
                "kind": "underpass_gravity",
                "lon": 140.10 + i * 0.01,
                "lat": 35.60 + i * 0.01,
                "dz": -1.0,
                "hist": 0,
                "road": "市道1号",
                "address": f"千葉県試験市{i}丁目",
                "review": {"status": "ok"},
            }
            for i in range(1, n + 1)
        ],
    }


base = make_spots(10)

# 素性の正しいデータで警告が出ないこと。
# ここが騒がしいと、本当の問題が埋もれて気づけなくなる。
out = run(base)
check("正しいデータでは範囲の警告が出ない",
      "範囲外に出ている地点" not in out and "極端に離れた地点" not in out,
      [l.strip() for l in out.splitlines() if "範囲外" in l][:2])

# 1件だけ遠くへ飛ぶ（ジオコーディングが別の県の同名地名を拾った場合）
far = copy.deepcopy(base)
far["spots"][3]["lon"], far["spots"][3]["lat"] = 135.50, 34.70   # 大阪あたり
out = run(far)
check("1件だけ遠くへ飛んだら気づく", "極端に離れた地点" in out,
      [l.strip() for l in out.splitlines() if "極端に離れ" in l][:1])
check("県の範囲外としても気づく", "範囲外に出ている地点" in out)

# 経度と緯度を取り違えた（よくある壊れ方）
swap = copy.deepcopy(base)
swap["spots"][5]["lon"], swap["spots"][5]["lat"] = 35.6, 140.1
out = run(swap)
check("経度と緯度の取り違えに気づく", "日本の範囲外に出ている地点" in out,
      [l.strip() for l in out.splitlines() if "日本の範囲外に" in l][:1])

# 県の範囲を登録していない県でも、仲間はずれ探しが働くこと。
# 東京を千葉のbboxで判定していた不具合（2026-09-17）の再発防止。
tokyo = make_spots(10, area="tokyo")
for i, s in enumerate(tokyo["spots"], 1):
    s["lon"], s["lat"] = 139.70 + i * 0.01, 35.68 + i * 0.005
    s["address"] = f"東京都試験区{i}"
out = run(tokyo)
check("未登録の県を、他県の範囲で誤判定しない",
      "範囲外に出ている地点" not in out,
      [l.strip() for l in out.splitlines() if "範囲外" in l][:2])
check("未登録の県ではその旨を伝える", "範囲は未登録" in out,
      [l.strip() for l in out.splitlines() if "未登録" in l][:1])

tokyo_far = copy.deepcopy(tokyo)
tokyo_far["spots"][2]["lon"], tokyo_far["spots"][2]["lat"] = 130.40, 33.59  # 福岡あたり
out = run(tokyo_far)
check("未登録の県でも遠くへ飛べば気づく", "極端に離れた地点" in out,
      [l.strip() for l in out.splitlines() if "極端に離れ" in l][:1])

# 地点が少ないときは中央値が当てにならないので、仲間はずれ探しはしない
few = make_spots(3)
few["spots"][0]["lon"] = 135.50
out = run(few)
check("地点が少なければ仲間はずれ探しはしない", "極端に離れた地点" not in out)

print("\n===== validate_spots の検証 =====")
for s in ok:
    print("  OK " + s)
for s in ng:
    print("  NG " + s)
print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
raise SystemExit(1 if ng else 0)
