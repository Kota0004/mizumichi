#!/usr/bin/env python3
"""道路冠水注意箇所の一覧PDF → 座標つき spots データ（docs/05-4-2 の「公的PDFルート」）

国土交通省 各地方整備局が公開している「道路冠水注意箇所マップ／一覧表」はPDFでしか
提供されていないため、機械可読なデータに起こす必要がある。本スクリプトは

    PDF → 表の抽出 → 種別の推定 → 住所のジオコーディング → spots JSON + レビュー用CSV

までを行う。**最後の人手レビューは省略しないこと**（docs/05-4-2）。
誤った地点を「危険」と表示するのは、見逃しとは別種の害になる。

使い方:
    # 1) まずPDFを入手（千葉県内: https://www.ktr.mlit.go.jp/chiba/chiba_index030.html）
    python3 tools/build_spots.py --pdf 一覧表.pdf --area chiba --out data/spots_chiba.json

    # ジオコーディングを行わず、抽出結果だけ確認する
    python3 tools/build_spots.py --pdf 一覧表.pdf --no-geocode --out /tmp/dry.json

    # どのページが一覧表かを調べる（地図と一覧表が混在していることがある）
    python3 tools/build_spots.py --pdf 資料.pdf --inspect

    # 表として抽出できない場合、何が読めているかを見る
    python3 tools/build_spots.py --pdf 一覧表.pdf --dump-text | head -50
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

GEOCODER = "https://msearch.gsi.go.jp/address-search/AddressSearch?q={}"

# 表ヘッダの表記ゆれ → 内部キー。
# 上にあるものから順に判定するので、より具体的な見出しを先に置くこと
# （例: 「道路種別」は冠水箇所の種別ではなく国道/県道/市道の区分なので、
#  「種別」で kind_raw に吸われる前に road_type として拾う）。
HEADER_MAP = {
    "no": ["no", "no.", "番号", "整理番号", "通し番号", "地点番号", "箇所番号"],
    "road_type": ["道路種別", "道路区分"],
    # 「所在」「地点」は 所在地 / 地点名 に部分一致してしまうので入れない
    "locality": ["地先名又は通称名", "地先名", "通称名", "地先"],
    "road": ["路線名", "路線", "道路名", "路線番号"],
    "name": ["箇所名", "地点名", "名称", "冠水箇所", "交差点名", "アンダーパス名", "箇所"],
    "address": ["所在地", "住所", "市町村名", "市町村", "市区町村", "位置"],
    "kind_raw": ["種別", "構造", "区分", "形式", "備考"],
    "admin": ["管理者", "道路管理者", "管理機関", "問合せ先"],
}

# 記載文言 → docs/04-3-1 の kind
KIND_RULES = [
    (("ポンプ",), "underpass_pump"),
    (("アンダーパス", "アンダー", "地下道", "立体交差", "ガード", "こ道橋", "掘割"), "underpass_gravity"),
    (("橋詰", "橋詰め", "取付", "橋梁"), "bridge_approach"),
    (("河川", "川沿", "堤内"), "river_adjacent"),
    (("低地", "窪地", "くぼ地", "凹部"), "lowland"),
]


def norm(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s).strip()


# 「神納4191-1（東京湾アクアライン連絡道ガード下）」の括弧内は通称。
# 住所としては邪魔だが、種別の手がかり（ガード下・アンダーパス等）になる。
PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")


# 「袖ケ浦１丁目１１番地先」の末尾「地先」は住所ではなく「その付近」の意。
# 付けたまま検索すると外れやすいので落とす（「１１番」までにする）。
CHISAKI_RE = re.compile(r"(地先|先)$")


def build_address(rec: dict, pref: str) -> str:
    """市町村名 + 地先名 から、ジオコーディングに渡す住所を組み立てる。

    norm() が NFKC 正規化するので、全角数字「１丁目」は「1丁目」になる。
    """
    city = norm(rec.get("address", ""))
    locality = PAREN_RE.sub("", norm(rec.get("locality", ""))).strip()
    locality = CHISAKI_RE.sub("", locality)

    # 地先名にすでに都道府県や市町村名が入っていることがある
    # （実データに「千葉県松戸市小山」がそのまま入っていて、
    #   市町村名を足した結果「千葉県松戸市千葉県松戸市小山」になった）
    if locality.startswith(pref):
        addr = locality
    elif city and locality.startswith(city):
        addr = locality
    else:
        addr = f"{city}{locality}"

    if addr and not re.match(r"^..[都道府県]", addr):
        addr = pref + addr
    return addr


def build_name(rec: dict, fallback: str) -> str:
    """地点名。通称（括弧内）があればそれを使う。"""
    locality = norm(rec.get("locality", ""))
    m = PAREN_RE.search(locality)
    if m and m.group(1):
        return m.group(1)
    return (norm(rec.get("name", "")) or PAREN_RE.sub("", locality).strip()
            or norm(rec.get("road", "")) or fallback)


def guess_kind(*texts: str) -> str:
    return guess_kind_with_reason(*texts)[0]


def guess_kind_with_reason(*texts: str) -> tuple[str, str]:
    """種別と、その根拠を返す。

    根拠を残すのは、レビューする人が「資料に書いてあった」のか
    「手がかりが無くて既定値になった」のかを区別できるようにするため。
    既定値のまま放置された地点は、閾値が実態と合わない可能性が残る。
    """
    blob = norm("".join(t or "" for t in texts))
    for keys, kind in KIND_RULES:
        hit = next((k for k in keys if k in blob), None)
        if hit:
            return kind, f"資料の「{hit}」から推定"
    # 判別できない場合は最も安全側（閾値が低い＝危険と判定されやすい）に倒す。
    # docs/03「安全側に倒す」より。
    return "underpass_gravity", "手がかりなし（既定値・要確認）"


def map_headers(header_row: list[str]) -> dict[int, str]:
    """表の1行目から「列インデックス → 内部キー」を作る。

    同じ内部キーに複数の列が当たることがある（例: 「市町村名」と、問合せ先の
    「市町村」欄）。後の列で上書きされると住所が壊れるので、先に出た列を優先する。
    """
    out: dict[int, str] = {}
    used: set[str] = set()
    for i, cell in enumerate(header_row):
        c = norm(cell).lower().replace("　", "")
        for key, alts in HEADER_MAP.items():
            if key in used:
                continue
            if any(a in c for a in alts):
                out[i] = key
                used.add(key)
                break
    return out


def inspect_pdf(pdf_path: Path) -> None:
    """PDFの中身を1ページずつ要約する。

    冠水注意箇所の資料は「地図（番号だけ）」と「一覧表（番号・路線名・所在地）」が
    別ファイルだったり、同じPDFの別ページに入っていたりする。
    どのページが一覧表なのかを機械的に見つけるための下調べ用。
    """
    import logging

    import pdfplumber

    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    LIST_HINTS = ("所在地", "路線", "箇所名", "住所", "市町村", "アンダーパス名")

    print(f"ファイル: {pdf_path.name}  ({pdf_path.stat().st_size/1024:.0f} KB)")
    candidates = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        print(f"ページ数: {len(pdf.pages)}\n")
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            hits = [h for h in LIST_HINTS if h in text]
            headers = {}
            for t in tables:
                for row in t[:3]:
                    m = map_headers([c or "" for c in row])
                    if len(m) > len(headers):
                        headers = m

            kind = "不明"
            if headers and len(headers) >= 3:
                kind = "★一覧表らしい"
                candidates.append(pno)
            elif hits:
                kind = "一覧表かもしれない（表としては読めていない）"
                candidates.append(pno)
            elif len(re.findall(r"\b\d{1,3}\b", text)) > 30 and len(text) < 800:
                kind = "地図（番号だけ）らしい"

            sample = re.sub(r"\s+", " ", text)[:100]
            print(f"--- {pno}ページ: {kind}")
            print(f"    文字数 {len(text)} / 表 {len(tables)} 個"
                  + (f" / 見つかった列 {sorted(set(headers.values()))}" if headers else "")
                  + (f" / 手がかり {hits}" if hits else ""))
            print(f"    冒頭: {sample or '(テキストなし)'}")

    print()
    if candidates:
        print(f"▶ 一覧表がありそうなページ: {candidates}")
        print(f"  次を実行して中身を確認してください:")
        print(f"      python3 tools/build_spots.py --pdf {pdf_path.name} "
              f"--dump-text --page {candidates[0]}")
    else:
        print("▶ このPDFに一覧表は見当たりません。")
        print("  「道路冠水注意箇所一覧表」という別のPDFを探して、そちらを使ってください。")
        print("  掲載元: https://www.ktr.mlit.go.jp/chiba/chiba_index030.html")


def dump_table(pdf_path: Path, only_page: int = 0, max_rows: int = 8) -> None:
    """表のセルを列番号つきでそのまま表示する。列の対応づけを確認するためのもの。"""
    import logging

    import pdfplumber

    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if only_page and pno != only_page:
                continue
            for ti, table in enumerate(page.extract_tables() or [], 1):
                print(f"=== {pno}ページ / 表{ti}: {len(table)}行 × "
                      f"{max(len(r) for r in table)}列")
                for ri, row in enumerate(table[:max_rows]):
                    print(f"  [{ri}] " + " | ".join(
                        f"{ci}:{(c or '').strip()[:22]!r}" for ci, c in enumerate(row)))
                    if ri == 0:
                        m = map_headers([c or "" for c in row])
                        if m:
                            print(f"       → 対応づけ: "
                                  + ", ".join(f"{k}列={v}" for k, v in sorted(m.items())))
                if len(table) > max_rows:
                    print(f"  … 残り {len(table) - max_rows} 行")
                print()


def extract_rows(pdf_path: Path, dump_text: bool = False, only_page: int = 0) -> list[dict]:
    import logging

    import pdfplumber

    # pdfminer は埋め込みフォントの不備で大量の警告を出すが、表の抽出には影響しない
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    rows: list[dict] = []
    carried_map: dict[int, str] = {}
    carried_ncols = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if only_page and pno != only_page:
                continue
            if dump_text:
                print(f"--- page {pno} ---")
                print(page.extract_text() or "(テキストなし)")
                continue

            for table in page.extract_tables() or []:
                if len(table) < 2:
                    continue
                # ヘッダ行を探す（先頭3行のうち最も多くのキーに当たる行）
                best_i, best_map = None, {}
                for i in range(min(3, len(table))):
                    m = map_headers([c or "" for c in table[i]])
                    if len(m) > len(best_map):
                        best_i, best_map = i, m

                # 地図ページにも凡例などの小さな表があり、たまたま1列だけ
                # 見出しに当たることがある。そこから地点を拾うと実在しない箇所が
                # 混ざるので、一覧表とみなすには3列以上の対応が必要とする。
                if len(best_map) >= MIN_HEADER_COLS:
                    carried_map, carried_ncols = best_map, len(table[best_i])
                elif carried_map and len(table[0]) == carried_ncols:
                    # 「(2/2)」のような続きのページには見出しが無い。
                    # 列数が同じなら直前の見出しを引き継ぐ（後半が丸ごと落ちるのを防ぐ）。
                    best_i, best_map = -1, carried_map
                else:
                    continue

                for raw in table[best_i + 1:]:
                    rec = {"_page": pno}
                    for idx, key in best_map.items():
                        if idx < len(raw):
                            rec[key] = norm(raw[idx])
                    # 「国 / 県 / 市町村」のような小見出し行を data として拾わないよう、
                    # 市町村名・路線名・地先名のどれかが入っている行だけを採用する
                    if any(rec.get(k) for k in ("name", "address", "road", "locality")):
                        rows.append(rec)

    if dump_text:
        return []

    if not rows:
        # 表として取れない場合のフォールバック: 行テキストから拾う
        rows = extract_rows_from_text(pdf_path, only_page=only_page)
    return rows


# 一覧表と判断するのに必要な、見出しの対応列数
MIN_HEADER_COLS = 3

LINE_RE = re.compile(
    r"^\s*(?P<no>\d{1,4})[\s.、]+(?P<rest>.+?)\s*$")

# ひらがな・カタカナ・漢字のいずれかを含むか（地図の番号の羅列を弾くために使う）
CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def extract_rows_from_text(pdf_path: Path, only_page: int = 0) -> list[dict]:
    """罫線のないPDF向けのフォールバック。「番号 + 本文」の行を拾う。

    冠水注意箇所の「地図」版PDFは番号だけが散らばっており、
    「87 61 35 9 70 …」のような行が大量にある。これを地点として拾ってしまうと
    実在しない危険箇所を作ってしまうため、路線名や地名にあたる部分に
    日本語（かな・漢字）が含まれる行だけを採用する。
    """
    import pdfplumber

    rows = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if only_page and pno != only_page:
                continue
            for line in (page.extract_text() or "").splitlines():
                m = LINE_RE.match(line)
                if not m:
                    continue
                rest = m.group("rest")
                parts = re.split(r"[\s　]{1,}", rest.strip())
                if len(parts) < 2:
                    continue
                if not any(CJK_RE.search(x) for x in parts[:3]):
                    continue        # 数字の羅列（地図の番号）は地点ではない
                rows.append({
                    "_page": pno,
                    "no": m.group("no"),
                    "road": norm(parts[0]),
                    "name": norm(parts[1]) if len(parts) > 1 else "",
                    "address": norm(parts[2]) if len(parts) > 2 else "",
                    "kind_raw": norm(" ".join(parts[3:])) if len(parts) > 3 else "",
                })
    return rows


def report_anomalies(rows: list[dict]) -> None:
    """抽出結果のおかしなところを警告する。件数が想定と違うときの手がかりになる。"""
    import collections

    nos = [r.get("no") for r in rows if r.get("no")]
    ints = []
    for n in nos:
        try:
            ints.append(int(n))
        except ValueError:
            pass

    problems = []
    dup = [n for n, c in collections.Counter(ints).items() if c > 1]
    if dup:
        problems.append(f"番号の重複: {sorted(dup)}")
    if ints:
        expected = set(range(min(ints), max(ints) + 1))
        missing = sorted(expected - set(ints))
        if missing:
            problems.append(f"番号の欠番: {missing}")
        problems.append(f"番号の範囲: {min(ints)}〜{max(ints)}（{len(set(ints))} 種類）")

    no_addr = [r.get("no") or "?" for r in rows
               if not norm(r.get("address", "")) and not norm(r.get("locality", ""))]
    if no_addr:
        problems.append(f"市町村名も地先名も無い行: {no_addr}（一覧表以外から拾った可能性）")

    kinds = collections.Counter(
        guess_kind_with_reason(r.get("kind_raw", ""), r.get("locality", ""),
                               r.get("name", ""), r.get("road", ""))[1]
        for r in rows)
    for reason, n in kinds.most_common():
        problems.append(f"種別の根拠「{reason}」: {n} 件")

    if problems:
        print("  点検:")
        for x in problems:
            print(f"    - {x}")


def geocode(query: str, session, sleep_s: float, cache: dict) -> dict | None:
    """国土地理院 住所検索API。無料・キー不要。連続アクセスは控えめに。"""
    if query in cache:
        return cache[query]
    url = GEOCODER.format(quote(query))
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:                                  # noqa: BLE001
        print(f"  ! ジオコーディング失敗: {query} ({e})", file=sys.stderr)
        cache[query] = None
        return None
    time.sleep(sleep_s)
    if not data:
        cache[query] = None
        return None
    top = data[0]
    res = {
        "lon": top["geometry"]["coordinates"][0],
        "lat": top["geometry"]["coordinates"][1],
        "title": top["properties"].get("title", ""),
        "n_candidates": len(data),
    }
    cache[query] = res
    return res


# 住所そのものの細かさ。番地まであるか、町名止まりかで、
# たとえ検索が「一致」しても点の精度は変わる。
DETAILED_RE = re.compile(r"\d+\s*(丁目|番地|番|条|-\s*\d+)")
DIGIT_RE = re.compile(r"\d")


def address_granularity(q: str) -> str:
    """住所の細かさ: detailed（番地まで）/ semi（数字あり）/ coarse（町名まで）"""
    if DETAILED_RE.search(q):
        return "detailed"
    if DIGIT_RE.search(q):
        return "semi"
    return "coarse"


def confidence_of(query: str, res: dict | None) -> tuple[float, str]:
    """ジオコーディング結果の確からしさ。低いものは人手レビューに回す。

    検索の一致度だけでは足りない。「千葉県市原市五井」のような町名までの住所は、
    そのまま一致しても点は町の中心どまりで、実際の地下道の位置ではない。
    そこで住所そのものの細かさで上限をかけ、レビューに回るようにする。
    """
    if not res:
        return 0.0, "ジオコーディング失敗"

    title = norm(res["title"])
    q = norm(query)

    if title == q:
        conf, note = 1.0, ""
    elif q.startswith(title) and len(title) >= len(q) - 6:
        conf, note = 0.8, "住所の末尾（丁目・番地）が一致しない"
    elif len(title) <= 6:
        conf, note = 0.3, "市区町村レベルまでしか特定できていない"
    else:
        conf, note = 0.5, "部分一致"

    gran = address_granularity(q)
    if gran == "coarse":
        # 町名までしか無いので、一致していても点は町の中心あたりにしかならない
        capped = min(conf, 0.4)
        if capped < conf:
            note = "資料の住所が町名までしかない（現地の位置は要確認）"
        conf = capped
    elif gran == "semi":
        capped = min(conf, 0.7)
        if capped < conf:
            note = "住所に丁目・番地が無い（現地の位置は要確認）"
        conf = capped

    return conf, note


def was_reviewed(spot: dict) -> bool:
    """人手が触った地点か（確認済みの印か、pending 以外の判断がある）。"""
    status = (spot.get("review") or {}).get("status", "pending")
    return bool(status != "pending" or (spot.get("evidence") or {}).get("verified_at"))


def preserve_reviewed(spots: list[dict], out: Path,
                      overwrite: bool = False) -> tuple[list[dict], int]:
    """既に人手で確認した地点は、取り込み直しても壊さない。

    資料は年に数回更新されるので取り込み直すことになるが、そのたびに
    レビュー結果（確認済みの印、手で直した座標、除外の判断）が消えると、
    同じ作業をやり直させることになる。IDが一致する地点のうち、
    人手が触ったものは古い記録をそのまま残す。

    資料から消えた地点は残さない。存在しない箇所を出し続ける方が危ない。
    """
    if overwrite or not out.exists():
        return spots, 0
    try:
        prev = json.loads(out.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"既存の出力を読めませんでした（無視して上書きします）: {e}", file=sys.stderr)
        return spots, 0
    reviewed = {o["id"]: o for o in prev.get("spots", [])
                if o.get("id") and was_reviewed(o)}
    if not reviewed:
        return spots, 0
    merged = [reviewed.get(sp["id"], sp) for sp in spots]
    return merged, sum(1 for sp in merged if sp["id"] in reviewed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, help="一覧表PDFのパス、または https:// のURL")
    ap.add_argument("--area", default="chiba", help="ID接頭辞に使うエリア名（既定: chiba）")
    ap.add_argument("--pref", default="千葉県", help="住所に都道府県が無い場合に補う（既定: 千葉県）")
    ap.add_argument("--out", default="data/spots.json")
    ap.add_argument("--review", default=None, help="レビュー用CSVの出力先（既定: --out と同名の .csv）")
    ap.add_argument("--no-geocode", action="store_true", help="座標を付けずに抽出結果だけ出す")
    ap.add_argument("--sleep", type=float, default=1.0, help="ジオコーディングの間隔[秒]")
    ap.add_argument("--limit", type=int, default=0, help="先頭N件だけ処理（動作確認用）")
    ap.add_argument("--dump-text", action="store_true", help="PDFのテキストを表示して終了")
    ap.add_argument("--inspect", action="store_true",
                    help="どのページが一覧表かを1ページずつ調べて表示する")
    ap.add_argument("--dump-table", action="store_true",
                    help="表のセルをそのまま表示する（列の並びを確認するとき）")
    ap.add_argument("--page", type=int, default=0,
                    help="指定ページだけを対象にする（0=全ページ）")
    ap.add_argument("--source", default="", help="出典表記（例: 国交省千葉国道事務所 2026-06-30版）")
    ap.add_argument("--overwrite-reviewed", action="store_true",
                    help="人手で確認済みの地点も上書きする（既定では残す）")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if str(args.pdf).startswith("http"):
        import requests
        pdf_path = Path("/tmp") / Path(args.pdf).name
        print(f"PDFを取得: {args.pdf}")
        pdf_path.write_bytes(requests.get(args.pdf, timeout=60).content)

    if not pdf_path.exists():
        print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
        return 1

    if args.inspect:
        inspect_pdf(pdf_path)
        return 0

    if args.dump_table:
        dump_table(pdf_path, args.page)
        return 0

    rows = extract_rows(pdf_path, dump_text=args.dump_text, only_page=args.page)
    if args.dump_text:
        return 0
    if args.limit:
        rows = rows[:args.limit]
    print(f"抽出した行数: {len(rows)}")
    report_anomalies(rows)
    if not rows:
        print("表を抽出できませんでした。--dump-text で中身を確認し、"
              "HEADER_MAP / LINE_RE を実際のPDFに合わせて調整してください。", file=sys.stderr)
        return 2

    session = None
    if not args.no_geocode:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "mizumichi-build-spots/0.1"

    cache: dict = {}
    spots, review = [], []
    for i, r in enumerate(rows, 1):
        sid = f"{args.area}-{int(r.get('no') or i):04d}"
        addr = build_address(r, args.pref)
        query = addr or f"{args.pref}{r.get('name', '')}"
        res = geocode(query, session, args.sleep, cache) if session else None
        conf, note = confidence_of(query, res)

        kind, kind_reason = guess_kind_with_reason(
            r.get("kind_raw", ""), r.get("locality", ""), r.get("name", ""), r.get("road", ""))
        spot = {
            "id": sid,
            "name": build_name(r, sid),
            "kind": kind,
            "kind_source": kind_reason,
            "lon": res["lon"] if res else None,
            "lat": res["lat"] if res else None,
            "dz": 0.0,                    # enrich_dem.py で埋める
            "hist": 0,                    # 履歴が判明したら更新
            "road": r.get("road", ""),
            "road_type": r.get("road_type", ""),
            "address": addr,
            "locality": r.get("locality", ""),
            "evidence": {
                "source": args.source or f"{pdf_path.name}",
                "confidence": round(conf, 2),
                "note": note,             # 確からしさが低い理由
                "verified_at": None,      # 人手レビューで日付を入れる
            },
        }
        spots.append(spot)
        review.append({
            "id": sid, "name": spot["name"], "road": spot["road"], "address": addr,
            "locality": r.get("locality", ""),
            "kind": spot["kind"], "kind_source": kind_reason,
            "kind_raw": r.get("kind_raw", ""),
            "lon": spot["lon"], "lat": spot["lat"],
            "geocode_title": res["title"] if res else "",
            "confidence": round(conf, 2), "note": note,
            "needs_review": "YES" if conf < 0.8 else "",
        })
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} 件処理")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    spots, kept = preserve_reviewed(spots, out, args.overwrite_reviewed)

    payload = {
        "version": "0.1.0",
        "generated_at": date.today().isoformat(),
        "area": args.area,
        "source": args.source or pdf_path.name,
        "note": "★人手レビュー前のデータです。evidence.verified_at が null の地点は未確認。",
        "spots": spots,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rev = Path(args.review) if args.review else out.with_suffix(".review.csv")
    with rev.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(review[0].keys()))
        w.writeheader()
        w.writerows(review)

    need = sum(1 for r in review if r["needs_review"])
    print(f"\n出力: {out}  ({len(spots)} 件)")
    if kept:
        print(f"  うち {kept} 件は人手で確認済みのため、以前の内容をそのまま残しました")
    print(f"レビュー用CSV: {rev}")
    print(f"要レビュー: {need} 件 / {len(review)} 件")
    print("\n次の手順: CSVを開いて座標を地図で確認し、正しいものは confidence を 1.0、"
          "evidence.verified_at に日付を入れてから本番データに昇格させてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
