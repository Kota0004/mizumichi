#!/usr/bin/env python3
"""気象庁アメダスの10分値を取り込み、各危険箇所の雨量と危険度を計算する（docs/05-4-1）

    latest_time.txt → map/{時刻}.json → 観測点をIDW内挿 → risk.py で危険度 → risk/latest.json

無料で「数値の」雨量が取れるのはアメダス（点観測）だけ。面の雨量は降水ナウキャストの
PNGタイルを読む必要があり（docs/02 R2）、それは別途 fetch_nowcast.py で扱う。
本スクリプトはまずアメダス単独で成立させ、タイルが使えるようになったら重みを足す構成。

⚠️ 気象庁のJSONは公式APIとして仕様保証されたものではない（docs/02 の注記）。
   スキーマが変わったら安全側に倒して取り込みを止める（誤った雨量で誤報を出さないため）。

使い方:
    python3 tools/fetch_amedas.py --spots data/spots_chiba.json --out data/risk_latest.json
    python3 tools/fetch_amedas.py --spots ... --fixture-dir tools/fixtures/amedas   # 通信なし
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import geo    # noqa: E402
import risk   # noqa: E402

BASE = "https://www.jma.go.jp/bosai/amedas"
URLS = {
    "latest_time": f"{BASE}/data/latest_time.txt",
    "table": f"{BASE}/const/amedastable.json",
    "map": f"{BASE}/data/map/{{ts}}.json",
}
JST = timezone(timedelta(hours=9))

# 観測点表をキャッシュしておく日数。観測点はめったに増減しない。
TABLE_CACHE_DAYS = 7

# 取り込む要素（キー → 内部名）。値は [観測値, 品質フラグ] の形で入っている。
ELEMS = {
    "precipitation10m": "r10",
    "precipitation1h": "r60",
    "precipitation3h": "r180",
}
QUALITY_OK = {0}          # 0 以外は欠測・資料不足として扱う


class Source:
    """通信あり／フィクスチャの切り替えを1か所に閉じ込める。"""

    def __init__(self, fixture_dir: Path | None = None, table_cache: Path | None = None):
        self.fixture_dir = fixture_dir
        self.table_cache = table_cache
        self.session = None
        if fixture_dir is None:
            import requests
            self.session = requests.Session()
            self.session.headers["User-Agent"] = "mizumichi-fetch-amedas/0.1"

    def _fixture(self, name: str):
        p = self.fixture_dir / name
        if not p.exists():
            raise FileNotFoundError(f"フィクスチャがありません: {p}")
        return p.read_text(encoding="utf-8")

    def latest_time(self) -> datetime:
        raw = (self._fixture("latest_time.txt") if self.fixture_dir
               else self.session.get(URLS["latest_time"], timeout=20).text)
        return datetime.fromisoformat(raw.strip())

    def table(self) -> dict:
        """観測点表。1MB近くあり、めったに変わらないのでキャッシュする。

        毎回取りに行くと、気象庁のサーバに無駄な負荷をかけるうえ、
        取り込み自体も遅くなる（10分ごとに動かす想定なので効いてくる）。
        """
        if self.fixture_dir:
            return json.loads(self._fixture("amedastable.json"))

        if self.table_cache and self.table_cache.exists():
            age_days = (time.time() - self.table_cache.stat().st_mtime) / 86400
            if age_days < TABLE_CACHE_DAYS:
                try:
                    return json.loads(self.table_cache.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass        # 壊れていたら取り直す

        raw = self.session.get(URLS["table"], timeout=60).text
        data = json.loads(raw)          # 先に検証してから保存する
        if self.table_cache:
            self.table_cache.parent.mkdir(parents=True, exist_ok=True)
            self.table_cache.write_text(raw, encoding="utf-8")
        return data

    def observations(self, ts: datetime) -> dict:
        name = ts.strftime("%Y%m%d%H%M%S")
        if self.fixture_dir:
            return json.loads(self._fixture(f"map_{name}.json"))
        r = self.session.get(URLS["map"].format(ts=name), timeout=30)
        r.raise_for_status()
        return r.json()


def station_points(table: dict, bbox: tuple[float, float, float, float] | None):
    """観測点表 → [(コード, lon, lat, 名称)]。bbox = (lon_min, lat_min, lon_max, lat_max)"""
    out = []
    for code, s in table.items():
        try:
            lon = geo.dms_to_deg(s["lon"])
            lat = geo.dms_to_deg(s["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        out.append((code, lon, lat, s.get("kjName", code)))
    return out


def value_of(obs: dict, code: str, key: str) -> float | None:
    """[観測値, 品質フラグ] から有効な値だけを取り出す。"""
    rec = obs.get(code)
    if not isinstance(rec, dict):
        return None
    v = rec.get(key)
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    val, flag = v[0], v[1]
    if val is None or flag not in QUALITY_OK:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def interpolate(spots, stations, obs, k=3, max_km=40.0):
    """各地点の雨量をIDWで内挿する。"""
    samples = {}
    for elem, name in ELEMS.items():
        samples[name] = [(lon, lat, value_of(obs, code, elem))
                         for code, lon, lat, _ in stations]

    results = []
    for s in spots:
        if s.get("lon") is None or s.get("lat") is None:
            continue
        if (s.get("review") or {}).get("status") == "excluded":
            continue        # レビューで除外した地点に危険度を出さない
        rain = {}
        for name, pts in samples.items():
            rain[name] = geo.idw(pts, s["lon"], s["lat"], k=k, max_km=max_km)
        results.append((s, rain))
    return results


def build_risk(spots, stations, obs, generated_at: datetime, k=3, max_km=40.0) -> dict:
    out_spots = {}
    n_missing = 0
    for s, rain in interpolate(spots, stations, obs, k=k, max_km=max_km):
        if rain.get("r60") is None:
            n_missing += 1
            continue
        spot = risk.Spot(id=s["id"], name=s.get("name", s["id"]), kind=s.get("kind", "lowland"),
                         lon=s["lon"], lat=s["lat"], dz=s.get("dz", 0.0), hist=s.get("hist", 0))
        r = risk.score(spot, r60=rain["r60"], r10=rain.get("r10"), r180=rain.get("r180"))
        out_spots[s["id"]] = {
            "level": r["level"],
            "score": round(r["score"], 1),
            "rain": {kk: (None if vv is None else round(vv, 1)) for kk, vv in rain.items()},
            "t60": round(r["t60"], 1),
            "kikikuru": r["k"],
            "reason": r["reason"],
        }
    return {
        "generated_at": generated_at.isoformat(),
        "valid_until": (generated_at + timedelta(minutes=10)).isoformat(),
        "source_freshness": {"amedas": generated_at.isoformat(), "nowcast": None, "kikikuru": None},
        "note": "アメダス（点観測）のIDW内挿のみ。面の雨量はナウキャストタイル導入後に合成する。",
        "spots": out_spots,
        "_stats": {"spots": len(out_spots), "missing": n_missing},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spots", required=True)
    ap.add_argument("--out", default="data/risk_latest.json")
    ap.add_argument("--fixture-dir", default=None, help="指定するとネットワークを使わない")
    ap.add_argument("--table-cache", default="data/amedastable_cache.json",
                    help=f"観測点表のキャッシュ先（{TABLE_CACHE_DAYS}日で取り直す）")
    ap.add_argument("--bbox", default="139.6,34.8,141.0,36.2",
                    help="観測点を絞る範囲 lon_min,lat_min,lon_max,lat_max（既定: 千葉県周辺）")
    ap.add_argument("--k", type=int, default=3, help="IDWで使う最寄り観測点の数")
    ap.add_argument("--max-km", type=float, default=40.0, help="これより遠い観測点は使わない")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    src = Source(Path(args.fixture_dir) if args.fixture_dir else None,
                 table_cache=Path(args.table_cache) if args.table_cache else None)
    spots = json.loads(Path(args.spots).read_text(encoding="utf-8"))["spots"]

    ts = src.latest_time()
    table = src.table()
    obs = src.observations(ts)
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    stations = station_points(table, bbox)

    if not stations:
        print("範囲内に観測点がありません。--bbox を確認してください。", file=sys.stderr)
        return 2

    result = build_risk(spots, stations, obs, ts, k=args.k, max_km=args.max_km)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(f"観測時刻: {ts.isoformat()}")
        print(f"範囲内の観測点: {len(stations)} 点")
        print(f"危険度を算出した地点: {result['_stats']['spots']} / "
              f"雨量を取れなかった地点: {result['_stats']['missing']}")
        by_level = {}
        for v in result["spots"].values():
            by_level[v["level"]] = by_level.get(v["level"], 0) + 1
        for lv in sorted(by_level, reverse=True):
            print(f"  レベル{lv} {risk.LEVELS[lv]['name']}: {by_level[lv]} 件")
        print(f"出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
