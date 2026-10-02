#!/usr/bin/env python3
"""fetch_amedas.py の検証（ネットワーク不要・フィクスチャ使用）

    python3 tools/test_fetch_amedas.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_amedas as fa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FX = Path(__file__).resolve().parent / "fixtures" / "amedas"
SPOTS = ROOT / "prototype" / "data" / "sample_spots.json"

ok, ng = [], []
def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


def test_source():
    src = fa.Source(FX)
    ts = src.latest_time()
    check("latest_time.txt を日時として読める", ts.strftime("%Y%m%d%H%M%S") == "20260819143000", str(ts))
    table = src.table()
    obs = src.observations(ts)
    check("観測点表と観測値を読める", len(table) >= 5 and len(obs) >= 5)
    return src, ts, table, obs


def test_station_points(table):
    bbox = (139.6, 34.8, 141.0, 36.2)
    pts = fa.station_points(table, bbox)
    codes = {c for c, *_ in pts}
    check("範囲内の4点だけが選ばれる（範囲外・不正な行は除外）",
          codes == {"90001", "90002", "90003", "90004"}, f"実際={sorted(codes)}")
    lon, lat = next((lo, la) for c, lo, la, _ in pts if c == "90001")
    check("[度,分] が10進度に変換される",
          abs(lon - 140.10333) < 1e-4 and abs(lat - 35.59833) < 1e-4, f"{lon:.5f},{lat:.5f}")


def test_value_of(obs):
    check("正常値を取り出せる", fa.value_of(obs, "90001", "precipitation1h") == 48.0)
    check("品質フラグが0以外なら欠測として扱う", fa.value_of(obs, "90004", "precipitation1h") is None)
    check("存在しない観測点は None", fa.value_of(obs, "99999", "precipitation1h") is None)
    check("存在しない要素は None", fa.value_of(obs, "90001", "snow") is None)


def test_interpolation_and_risk(table, obs, ts):
    spots = json.loads(SPOTS.read_text(encoding="utf-8"))["spots"]
    stations = fa.station_points(table, (139.6, 34.8, 141.0, 36.2))
    res = fa.build_risk(spots, stations, obs, ts)

    check("全14地点の危険度が算出される", res["_stats"]["spots"] == 14,
          f"実際={res['_stats']['spots']} / 欠測={res['_stats']['missing']}")

    vals = [v["rain"]["r60"] for v in res["spots"].values()]
    check("内挿された時間雨量が観測点の最小〜最大の範囲に収まる",
          all(4.0 - 1e-6 <= v <= 48.0 + 1e-6 for v in vals),
          f"範囲={min(vals):.1f}〜{max(vals):.1f}mm/h（観測点は 4.0〜48.0）")

    # 観測点Aの近く（雨が強い）と観測点Cの近く（雨が弱い）で差が出るか
    s01 = res["spots"]["S-01"]      # 140.1065,35.6108 … 観測点A(140.103,35.598)のすぐ近く
    s10 = res["spots"]["S-10"]      # 140.0330,35.5820 … 観測点Cに比較的近い
    check("雨の強い観測点の近くほど雨量が大きく内挿される",
          s01["rain"]["r60"] > s10["rain"]["r60"],
          f"S-01={s01['rain']['r60']}mm/h > S-10={s10['rain']['r60']}mm/h")

    check("判定理由が入っている", all(v["reason"] for v in res["spots"].values()))
    check("地点ごとの閾値 t60 が出力される", all(v["t60"] > 0 for v in res["spots"].values()))
    check("鮮度情報 generated_at / valid_until がある",
          res["generated_at"] and res["valid_until"])

    lv = sorted({v["level"] for v in res["spots"].values()})
    check("同じ雨でも地点によりレベルが分かれる", len(lv) >= 2, f"出現レベル={lv}")
    return res


def test_table_cache():
    """観測点表を毎回取りに行かない（気象庁への無駄な負荷を避ける）。"""
    import time as _t

    calls = []

    class FakeSession:
        def get(self, url, timeout=0):
            calls.append(url)
            class R:
                text = json.dumps({"90001": {"lat": [35, 0], "lon": [140, 0], "kjName": "テスト"}})
            return R()

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "table.json"
        src = fa.Source(None, table_cache=cache)
        src.session = FakeSession()

        src.table()
        check("初回は取りに行く", len(calls) == 1, f"{len(calls)} 回")
        check("キャッシュが作られる", cache.exists())

        src.table()
        check("2回目はキャッシュを使う（再取得しない）", len(calls) == 1, f"{len(calls)} 回")

        # 期限切れは取り直す
        old_time = _t.time() - (fa.TABLE_CACHE_DAYS + 1) * 86400
        os.utime(cache, (old_time, old_time))
        src.table()
        check(f"{fa.TABLE_CACHE_DAYS}日を過ぎたら取り直す", len(calls) == 2, f"{len(calls)} 回")

        # 壊れていたら取り直す
        cache.write_text("{壊れたJSON", encoding="utf-8")
        src.table()
        check("キャッシュが壊れていたら取り直す", len(calls) == 3, f"{len(calls)} 回")


def test_excluded_skipped():
    """レビューで除外した地点には危険度を出さない。"""
    spots = json.loads(SPOTS.read_text(encoding="utf-8"))["spots"]
    marked = [dict(x) for x in spots]
    marked[0]["review"] = {"status": "excluded"}
    src = fa.Source(FX)
    ts, table, obs = src.latest_time(), src.table(), src.observations(src.latest_time())
    stations = fa.station_points(table, (139.6, 34.8, 141.0, 36.2))
    res = fa.build_risk(marked, stations, obs, ts)
    check("除外した地点は危険度に含まれない",
          marked[0]["id"] not in res["spots"], marked[0]["id"])
    check("残りの地点は計算される", res["_stats"]["spots"] == len(spots) - 1,
          f"{res['_stats']['spots']} / 期待 {len(spots)-1}")


def test_cli():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "risk.json"
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "fetch_amedas.py"),
             "--spots", str(SPOTS), "--out", str(out), "--fixture-dir", str(FX)],
            capture_output=True, text=True)
        check("CLIが正常終了する", r.returncode == 0, (r.stderr or "").strip()[:120])
        check("出力JSONが生成される", out.exists() and json.loads(out.read_text(encoding="utf-8"))["spots"])
        if r.returncode == 0:
            print("\n--- CLI 出力 ---")
            print(r.stdout.rstrip())
            print("----------------\n")


def main():
    src, ts, table, obs = test_source()
    test_station_points(table)
    test_value_of(obs)
    res = test_interpolation_and_risk(table, obs, ts)
    test_table_cache()
    test_excluded_skipped()
    test_cli()

    print("===== fetch_amedas.py 検証 =====")
    for s in ok:
        print("  ✅ " + s)
    for s in ng:
        print("  ❌ " + s)
    print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
    if res:
        print("\n--- 危険度の内訳（フィクスチャの雨量による） ---")
        for sid, v in sorted(res["spots"].items(), key=lambda kv: -kv[1]["score"])[:5]:
            print(f"  {sid}: Lv{v['level']} S={v['score']} r60={v['rain']['r60']}mm/h t60={v['t60']}mm/h")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
