#!/usr/bin/env python3
"""build_spots.py の検証（ネットワーク不要）

    python3 tools/test_build_spots.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_spots as bs  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_kansui_list.pdf"
FIXTURE_MAP = Path(__file__).resolve().parent / "fixtures" / "sample_map_then_list.pdf"

ok, ng = [], []
def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


def test_guess_kind():
    cases = {
        "アンダーパス": "underpass_gravity",
        "アンダーパス（ポンプ有）": "underpass_pump",
        "ガード下": "underpass_gravity",
        "立体交差": "underpass_gravity",
        "地下道": "underpass_gravity",
        "橋詰め": "bridge_approach",
        "低地": "lowland",
        "くぼ地": "lowland",
        "河川隣接": "river_adjacent",
        "": "underpass_gravity",          # 不明なときは安全側（閾値が低い方）
    }
    for text, want in cases.items():
        got = bs.guess_kind(text)
        check(f"種別推定 {text or '(空)'!r} → {want}", got == want, f"実際={got}")


def test_norm_and_headers():
    check("全角/空白の正規化", bs.norm(" 千葉　市 ") == "千葉市", bs.norm(" 千葉　市 "))
    hm = bs.map_headers(["番号", "路線名", "箇所名", "所在地", "種別", "管理者"])
    check("ヘッダ対応づけ", hm == {0: "no", 1: "road", 2: "name", 3: "address", 4: "kind_raw", 5: "admin"}, str(hm))
    hm2 = bs.map_headers(["No.", "道路名", "地点名", "住所"])
    check("ヘッダの表記ゆれ吸収", hm2 == {0: "no", 1: "road", 2: "name", 3: "address"}, str(hm2))


def test_confidence():
    q = "千葉県千葉市中央区サンプル町1-1"
    check("完全一致は confidence 1.0",
          bs.confidence_of(q, {"title": q, "lon": 0, "lat": 0})[0] == 1.0)
    check("市区町村止まりは低信頼",
          bs.confidence_of(q, {"title": "千葉市", "lon": 0, "lat": 0})[0] <= 0.3)
    check("失敗は 0.0", bs.confidence_of(q, None)[0] == 0.0)

    # 住所の細かさ
    for addr, want in [("千葉県習志野市袖ケ浦1丁目11番", "detailed"),
                       ("千葉県袖ケ浦市神納4191-1", "detailed"),
                       ("千葉県市原市五井2", "semi"),
                       ("千葉県市原市五井", "coarse"),
                       ("千葉県市原市", "coarse")]:
        got = bs.address_granularity(addr)
        check(f"住所の細かさ {addr}", got == want, f"実際={got} 期待={want}")

    # 粗い住所は、たとえ完全一致でも高信頼にしない
    coarse = "千葉県市原市五井"
    c, note = bs.confidence_of(coarse, {"title": coarse})
    check("町名までの住所は完全一致でも上限0.4", c <= 0.4, f"{c} / {note}")
    check("その理由が注記に出る", "町名までしかない" in note, note)

    semi = "千葉県市原市五井2"
    c2, note2 = bs.confidence_of(semi, {"title": semi})
    check("丁目・番地が無い住所は上限0.7", c2 <= 0.7, f"{c2} / {note2}")

    detailed = "千葉県習志野市袖ケ浦1丁目11番"
    c3, _ = bs.confidence_of(detailed, {"title": detailed})
    check("番地まである住所の完全一致は1.0のまま", c3 == 1.0, str(c3))


def test_extract():
    if not FIXTURE.exists():
        check("フィクスチャPDFが存在する", False,
              "node tools/fixtures/make_fixture_pdf.js で生成してください")
        return
    rows = bs.extract_rows(FIXTURE)
    check("PDFから8行抽出できる", len(rows) == 8, f"実際={len(rows)}行")
    if len(rows) == 8:
        kinds = [bs.guess_kind(r.get("kind_raw", ""), r.get("name", ""), r.get("road", ""))
                 for r in rows]
        want = ["underpass_gravity", "underpass_pump", "bridge_approach", "lowland",
                "underpass_gravity", "river_adjacent", "lowland", "underpass_gravity"]
        check("種別が期待どおり推定される", kinds == want, f"実際={kinds}")
        check("所在地が読めている", all(r.get("address") for r in rows))


def test_map_and_list_pdf():
    """1ページ目が地図（番号だけ）、2ページ目が一覧表、という実物に近い構成。

    実際の国交省の資料がこの形（地図PDFに住所が入っていない）だったため、
    どのページが一覧表かを見つけられること、そのページだけを読めることを確認する。
    """
    if not FIXTURE_MAP.exists():
        check("地図＋一覧表のフィクスチャがある", False,
              "node tools/fixtures/make_fixture_map_pdf.js で生成してください")
        return

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bs.inspect_pdf(FIXTURE_MAP)
    out = buf.getvalue()
    check("--inspect が一覧表のページ(2)を指す", "一覧表がありそうなページ: [2]" in out,
          [ln for ln in out.splitlines() if "ありそうな" in ln])
    check("--inspect が1ページ目を地図と判定する", "1ページ: 地図（番号だけ）らしい" in out,
          [ln for ln in out.splitlines() if ln.startswith("--- 1ページ")])

    rows_all = bs.extract_rows(FIXTURE_MAP)
    check("見出しの無い続きページ(2/2)も読み、全5行が取れる",
          len(rows_all) == 5, f"実際={len(rows_all)}行")

    rows_p2 = bs.extract_rows(FIXTURE_MAP, only_page=2)
    check("--page 2 でそのページの3行だけ読める", len(rows_p2) == 3, f"実際={len(rows_p2)}行")

    rows_p1 = bs.extract_rows(FIXTURE_MAP, only_page=1)
    check("--page 1（地図）からは行が取れない", len(rows_p1) == 0, f"実際={len(rows_p1)}行")
    check("地図ページの凡例表（見出し1列だけ）を一覧表と誤認しない",
          len(rows_all) == 5, f"実際={len(rows_all)}行（凡例から拾うと6行になる）")

    if len(rows_all) == 5:
        addrs = [bs.build_address(r, "サンプル県") for r in rows_all]
        names = [bs.build_name(r, "") for r in rows_all]
        kinds = [bs.guess_kind(r.get("kind_raw", ""), r.get("locality", ""),
                               r.get("name", ""), r.get("road", "")) for r in rows_all]
        check("市町村名＋地先名から住所を組み立てる（括弧の通称は除く）",
              addrs[3] == "サンプル県テスト市テスト4191-1", addrs[3])
        check("括弧内の通称を地点名にする", names[3] == "サンプル連絡道ガード下", names[3])
        check("通称から種別を推定する（ガード下→自然排水アンダーパス）",
              kinds[3] == "underpass_gravity", kinds[3])
        check("ポンプの有無を種別に反映する", kinds[1] == "underpass_pump", kinds[1])
        check("続きページの行も住所になる", addrs[4].startswith("サンプル県ダミー町"), addrs[4])
        check("「問合せ先」の小見出し行（国/県/市町村）を地点として拾わない",
              all("国" != (r.get("admin") or "") or r.get("locality") for r in rows_all),
              str([r.get("admin") for r in rows_all]))
        check("全角数字と改行が正規化される（１丁目→1丁目）",
              addrs[0] == "サンプル県サンプル市サンプル町1丁目11番", addrs[0])
        check("末尾の「地先」を住所から落とす", not addrs[0].endswith("地先"), addrs[0])


def test_min_header_cols():
    """見出しが2列以下しか当たらない表は一覧表とみなさない。"""
    check("1列だけの見出しは一覧表ではない",
          len(bs.map_headers(["箇所", "色"])) < bs.MIN_HEADER_COLS,
          str(bs.map_headers(["箇所", "色"])))
    check("実物の見出しは一覧表と判定される",
          len(bs.map_headers(["No.", "市町村名", "道路種別", "問合せ先", "", "",
                              "路線名", "地先名又は通称名"])) >= bs.MIN_HEADER_COLS)


def test_anomaly_report():
    """点検の出力（欠番・重複・住所なし）"""
    import contextlib
    import io
    rows = [
        {"no": "1", "address": "習志野市", "locality": "袖ケ浦1丁目"},
        {"no": "3", "address": "市原市", "locality": "五井"},
        {"no": "3", "address": "市原市", "locality": "五井"},
        {"no": "9", "address": "", "locality": ""},
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bs.report_anomalies(rows)
    out = buf.getvalue()
    check("番号の重複を報告する", "番号の重複: [3]" in out, out.strip()[:60])
    check("番号の欠番を報告する", "欠番" in out and "2" in out, out.strip()[:80])
    check("市町村名も地先名も無い行を報告する", "市町村名も地先名も無い行" in out)



def test_chisaki_and_paren():
    """実物にある住所表記のくせを個別に確認する。"""
    cases = [
        ({"address": "習志野市", "locality": "袖ケ浦１丁目１１番地先"},
         "千葉県習志野市袖ケ浦1丁目11番"),
        ({"address": "市原市", "locality": "五井（五井アンダーパス）"},
         "千葉県市原市五井"),
        ({"address": "袖ケ浦市", "locality": "神納4191-1（東京湾アクアライン連絡道ガード下）"},
         "千葉県袖ケ浦市神納4191-1"),
        ({"address": "千葉県銚子市", "locality": "松岸町1-1"},
         "千葉県銚子市松岸町1-1"),          # すでに県が付いていれば足さない
    ]
    for rec, want in cases:
        got = bs.build_address(rec, "千葉県")
        check(f"住所の組み立て {rec['locality'][:14]}", got == want, f"実際={got} 期待={want}")

    check("括弧内の通称を地点名にする（五井アンダーパス）",
          bs.build_name({"address": "市原市", "locality": "五井（五井アンダーパス）"}, "-")
          == "五井アンダーパス")
    check("通称が無ければ地先名をそのまま地点名にする",
          bs.build_name({"address": "習志野市", "locality": "袖ケ浦１丁目１１番地先"}, "-")
          == "袖ケ浦1丁目11番地先")


def test_header_mapping_real():
    """実物の見出し（国交省 千葉県版）で列が正しく対応づくか。"""
    header = ["No.", "市町村名", "道路種別", "路線名", "地先名又は通称名", "国", "県", "市町村"]
    m = bs.map_headers(header)
    check("実物の見出しが正しく対応づく",
          m.get(0) == "no" and m.get(1) == "address" and m.get(2) == "road_type"
          and m.get(3) == "road" and m.get(4) == "locality",
          str(m))
    check("問合せ先の「市町村」欄が住所を上書きしない", m.get(7) != "address", str(m.get(7)))
    check("「道路種別」が冠水箇所の種別として誤読されない", m.get(2) == "road_type", str(m.get(2)))


def test_geocode_with_fake_session():
    """ネットワークを使わずジオコーディング処理の形を確認する。"""
    class FakeResp:
        def __init__(self, data): self._d = data
        def raise_for_status(self): pass
        def json(self): return self._d

    class FakeSession:
        calls = []
        def get(self, url, timeout=0):
            FakeSession.calls.append(url)
            return FakeResp([{
                "geometry": {"coordinates": [140.1065, 35.6108]},
                "properties": {"title": "千葉県千葉市中央区サンプル町1-1"},
            }])

    cache = {}
    s = FakeSession()
    res = bs.geocode("千葉県千葉市中央区サンプル町1-1", s, 0.0, cache)
    check("ジオコーディング結果を座標に変換できる",
          res and abs(res["lon"] - 140.1065) < 1e-9 and abs(res["lat"] - 35.6108) < 1e-9, str(res))
    bs.geocode("千葉県千葉市中央区サンプル町1-1", s, 0.0, cache)
    check("同一クエリはキャッシュされ再リクエストしない", len(FakeSession.calls) == 1,
          f"リクエスト数={len(FakeSession.calls)}")


def main():
    test_guess_kind()
    test_norm_and_headers()
    test_confidence()
    test_extract()
    test_map_and_list_pdf()
    test_header_mapping_real()
    test_chisaki_and_paren()
    test_min_header_cols()
    test_anomaly_report()
    test_geocode_with_fake_session()
    print("===== build_spots.py 検証 =====")
    for s in ok:
        print("  ✅ " + s)
    for s in ng:
        print("  ❌ " + s)
    print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
