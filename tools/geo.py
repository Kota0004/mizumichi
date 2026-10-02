"""地理計算まわりの共通処理。

- Web メルカトルのタイル座標変換（気象庁タイル・地理院タイル共通）
- 地理院 PNG 標高タイルのデコード
- 逆距離加重（IDW）内挿
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# 標高タイルの種類（解像度の高い順に試す）— docs/02 S5
DEM_TILES = [
    # (名前, URLテンプレート, 最大ズーム)
    ("dem5a", "https://cyberjapandata.gsi.go.jp/xyz/dem5a_png/{z}/{x}/{y}.png", 15),
    ("dem5b", "https://cyberjapandata.gsi.go.jp/xyz/dem5b_png/{z}/{x}/{y}.png", 15),
    ("dem",   "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png",   14),
]

NODATA = float("nan")


def lonlat_to_tile_pixel(lon: float, lat: float, z: int) -> tuple[int, int, int, int]:
    """緯度経度 → (タイルX, タイルY, ピクセルX, ピクセルY)。docs/04-2 と同じ式。"""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    tx, ty = int(x), int(y)
    px = min(255, int((x - tx) * 256))
    py = min(255, int((y - ty) * 256))
    return tx, ty, px, py


def decode_dem_pixel(r: int, g: int, b: int, u: float = 0.01) -> float:
    """地理院 PNG 標高タイルの画素値 → 標高[m]。

    仕様（国土地理院「標高タイルの詳細仕様」）:
        x = R * 65536 + G * 256 + B
        x <  2^23 → h = x * u
        x == 2^23 → 無効値（NA）
        x >  2^23 → h = (x - 2^24) * u
    """
    x = r * 65536 + g * 256 + b
    if x == 2 ** 23:
        return NODATA
    if x < 2 ** 23:
        return x * u
    return (x - 2 ** 24) * u


def idw(samples: Sequence[tuple[float, float, float]], lon: float, lat: float,
        k: int = 3, power: float = 2.0, max_km: float = 40.0) -> float | None:
    """逆距離加重内挿。samples は (lon, lat, 値) の並び。

    最寄り k 点を距離の -power 乗で重み付けする。max_km より遠い点しかなければ None。
    """
    scored = []
    for s_lon, s_lat, val in samples:
        if val is None:
            continue
        d = haversine_m(lon, lat, s_lon, s_lat)
        scored.append((d, val))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    scored = [s for s in scored[:k] if s[0] <= max_km * 1000]
    if not scored:
        return None
    if scored[0][0] < 1.0:          # ほぼ同一地点
        return scored[0][1]
    num = sum(v / (d ** power) for d, v in scored)
    den = sum(1 / (d ** power) for d, v in scored)
    return num / den


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def local_xy(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    """緯度経度 → 局所的な平面座標[m]。数百m〜数kmの範囲なら十分な精度。"""
    return (lon * 111_320.0 * math.cos(math.radians(lat0)), lat * 110_540.0)


def xy_to_lonlat(x: float, y: float, lat0: float) -> tuple[float, float]:
    return (x / (111_320.0 * math.cos(math.radians(lat0))), y / 110_540.0)


def point_segment_distance(plon: float, plat: float,
                           alon: float, alat: float,
                           blon: float, blat: float) -> tuple[float, float, float]:
    """点Pと線分ABの距離[m]、および線分上の最近接点(lon, lat)を返す。"""
    lat0 = plat
    px, py = local_xy(plon, plat, lat0)
    ax, ay = local_xy(alon, alat, lat0)
    bx, by = local_xy(blon, blat, lat0)

    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return haversine_m(plon, plat, alon, alat), alon, alat

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))          # 線分の外には出さない
    cx, cy = ax + t * dx, ay + t * dy
    clon, clat = xy_to_lonlat(cx, cy, lat0)
    return haversine_m(plon, plat, clon, clat), clon, clat


def nearest_on_ways(lon: float, lat: float, ways: list[dict],
                    max_m: float = 300.0) -> dict | None:
    """複数の線（[{"geometry": [{"lon","lat"}...], ...}]）のうち最も近い点を返す。

    戻り値: {"distance_m", "lon", "lat", "way"} / 範囲内に無ければ None
    """
    best = None
    for way in ways:
        pts = way.get("geometry") or []
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            d, clon, clat = point_segment_distance(lon, lat, a["lon"], a["lat"],
                                                   b["lon"], b["lat"])
            if d <= max_m and (best is None or d < best["distance_m"]):
                best = {"distance_m": d, "lon": clon, "lat": clat, "way": way}
    return best


def dms_to_deg(pair: Iterable[float]) -> float:
    """気象庁アメダスの [度, 分] 形式を10進度へ。"""
    d, m = list(pair)[:2]
    return float(d) + float(m) / 60.0


def ring_offsets(radius_m: float, lat: float, n: int = 8) -> list[tuple[float, float]]:
    """指定半径の円周上に n 点の (Δlon, Δlat) を返す。相対標高の計算に使う。"""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return [(dlon * math.cos(2 * math.pi * i / n), dlat * math.sin(2 * math.pi * i / n))
            for i in range(n)]
