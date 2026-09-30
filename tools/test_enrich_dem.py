#!/usr/bin/env python3
"""enrich_dem.py の検証（ネットワーク不要・合成タイルを使用）

    python3 tools/test_enrich_dem.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich_dem  # noqa: E402
import geo  # noqa: E402

ok, ng = [], []
def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


def encode(h_m: float):
    """標高[m] → 地理院PNG標高タイルの RGB（decode_dem_pixel の逆変換）"""
    x = int(round(h_m / 0.01))
    if x < 0:
        x += 2 ** 24
    return (x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF


class FakeSampler(enrich_dem.ElevationSampler):
    """中心が窪地、周囲が平坦、という合成地形を返す。"""

    def __init__(self, center_lon, center_lat, center_h, around_h, **kw):
        super().__init__(**kw)
        self.c = (center_lon, center_lat)
        self.center_h, self.around_h = center_h, around_h

    def elevation(self, lon, lat):
        from PIL import Image
        d = geo.haversine_m(lon, lat, *self.c)
        h = self.center_h if d < 30 else self.around_h
        img = Image.new("RGB", (1, 1), encode(h))
        return geo.decode_dem_pixel(*img.getpixel((0, 0)))


def test_encode_decode_roundtrip():
    for h in [0.0, 0.01, 1.23, 25.6, 1234.56, -0.01, -5.5]:
        got = geo.decode_dem_pixel(*encode(h))
        check(f"標高 {h}m のエンコード/デコード往復", abs(got - h) < 1e-6, f"実際={got}")


def test_nodata():
    check("無効値 (128,0,0) は NaN", geo.decode_dem_pixel(128, 0, 0) != geo.decode_dem_pixel(128, 0, 0))


def test_relative_elevation():
    lon, lat = 140.1065, 35.6108
    s = FakeSampler(lon, lat, center_h=3.0, around_h=5.0)
    dz, info = enrich_dem.relative_elevation(s, lon, lat, radius_m=100.0)
    check("窪地で dz が負になる", dz is not None and abs(dz - (-2.0)) < 1e-6, f"dz={dz}")
    check("周囲8点すべてサンプリングされる", info.get("ring_n") == 8, str(info))

    s2 = FakeSampler(lon, lat, center_h=7.0, around_h=5.0)
    dz2, _ = enrich_dem.relative_elevation(s2, lon, lat, radius_m=100.0)
    check("高台で dz が正になる", dz2 is not None and abs(dz2 - 2.0) < 1e-6, f"dz={dz2}")


def test_dz_affects_threshold():
    """dz が閾値 T60 に効くこと（docs/04-3-2 の補正）"""
    import risk
    a = risk.Spot(id="a", name="窪地", kind="underpass_gravity", lon=0, lat=0, dz=-2.0, hist=0)
    b = risk.Spot(id="b", name="高台", kind="underpass_gravity", lon=0, lat=0, dz=+2.0, hist=0)
    check("窪地の方が閾値が低い（危険と判定されやすい）", a.t60 < b.t60,
          f"窪地 {a.t60:.1f} < 高台 {b.t60:.1f} mm/h")


def test_tunnel_not_flagged():
    """トンネル・立体の大きな高低差を「座標の誤り」として扱わない。

    標高データは地表（山や築堤）を測るので、山を貫くトンネルでは
    道路面ではなく上の地形の高さが出る。実データでも
    「利根山隧道 -9.6m」「竹岡立体地下道 +15.5m」が警告になっていた。
    """
    check("トンネルは警告対象から外れる", bool(enrich_dem.TUNNELISH_RE.search("利根山隧道")))
    check("立体も外れる", bool(enrich_dem.TUNNELISH_RE.search("竹岡立体地下道")))
    check("ガード下は対象のまま（構造上の理由が無い）",
          not enrich_dem.TUNNELISH_RE.search("JR常磐線船取ガード"))
    check("地下道は対象のまま",
          not enrich_dem.TUNNELISH_RE.search("原木地下道"))


def main():
    test_encode_decode_roundtrip()
    test_nodata()
    test_relative_elevation()
    test_dz_affects_threshold()
    test_tunnel_not_flagged()
    print("===== enrich_dem.py 検証 =====")
    for s in ok:
        print("  ✅ " + s)
    for s in ng:
        print("  ❌ " + s)
    print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
