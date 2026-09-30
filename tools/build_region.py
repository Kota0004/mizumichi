#!/usr/bin/env python3
"""地方整備局の索引ページから県別PDFを集めて、まとめて取り込む。

道路冠水注意箇所は全国統一のデータが存在せず、地方整備局ごとにPDFで
公表されている（重ねるハザードマップの掲載状況一覧も「各地方整備局等データ」
と書いてあるだけだった）。そこで整備局ごとに索引ページを登録しておき、
そこから県別PDFのリンクを毎回読んで取り込む。

PDFのURLは更新のたびに通し番号が変わる（例 000752021.pdf）ので、
URLを直接書かずに索引ページから引く。こうしておくと、資料が更新されても
このツールを直さずに追従できる。

使い方:
    # 索引ページを読んで、取り込める県を一覧する（取得はしない）
    python3 tools/build_region.py --region kanto --list

    # 取り込む（県ごとに data/spots_<slug>.json を作る）
    python3 tools/build_region.py --region kanto --outdir data

    # 1県だけ試す
    python3 tools/build_region.py --region kanto --only 東京都 --outdir data
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGIONS_PATH = HERE.parent / "data" / "regions.json"

# 都道府県名 → ID接頭辞に使うローマ字
PREF_SLUG = {
    "北海道": "hokkaido", "青森県": "aomori", "岩手県": "iwate", "宮城県": "miyagi",
    "秋田県": "akita", "山形県": "yamagata", "福島県": "fukushima",
    "茨城県": "ibaraki", "栃木県": "tochigi", "群馬県": "gunma", "埼玉県": "saitama",
    "千葉県": "chiba", "東京都": "tokyo", "神奈川県": "kanagawa",
    "新潟県": "niigata", "富山県": "toyama", "石川県": "ishikawa", "福井県": "fukui",
    "山梨県": "yamanashi", "長野県": "nagano", "岐阜県": "gifu", "静岡県": "shizuoka",
    "愛知県": "aichi", "三重県": "mie", "滋賀県": "shiga", "京都府": "kyoto",
    "大阪府": "osaka", "兵庫県": "hyogo", "奈良県": "nara", "和歌山県": "wakayama",
    "鳥取県": "tottori", "島根県": "shimane", "岡山県": "okayama", "広島県": "hiroshima",
    "山口県": "yamaguchi", "徳島県": "tokushima", "香川県": "kagawa",
    "愛媛県": "ehime", "高知県": "kochi", "福岡県": "fukuoka", "佐賀県": "saga",
    "長崎県": "nagasaki", "熊本県": "kumamoto", "大分県": "oita", "宮崎県": "miyazaki",
    "鹿児島県": "kagoshima", "沖縄県": "okinawa",
}

# リンクテキストの先頭に現れる都道府県名を拾う。
# 「神奈川県・川崎市・横浜市・相模原市 令和8年6月1日【更新】[PDF:716KB]」のように
# 政令市が続くことがあるので、先頭一致で県名だけを取る。
PREF_RE = re.compile("|".join(sorted(PREF_SLUG, key=len, reverse=True)))

LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def load_regions() -> dict:
    return json.loads(REGIONS_PATH.read_text(encoding="utf-8"))


def fetch(url: str) -> str:
    import requests
    r = requests.get(url, timeout=60,
                     headers={"User-Agent": "mizumichi-build-region/0.1"})
    r.raise_for_status()
    # 整備局のページは Shift_JIS のことがある。requests の推定に任せず、
    # 明示されていなければ apparent_encoding を使う。
    if not re.search(r"charset", r.headers.get("Content-Type", ""), re.I):
        r.encoding = r.apparent_encoding
    return r.text


def find_pdfs(html: str, base_url: str) -> list[dict]:
    """索引ページのHTMLから (都道府県, PDF URL, リンクテキスト) を拾う。"""
    found: list[dict] = []
    seen: set[str] = set()
    for href, text in LINK_RE.findall(html):
        if ".pdf" not in href.lower():
            continue
        label = re.sub(r"\s+", " ", TAG_RE.sub("", text)).strip()
        m = PREF_RE.search(label)
        if not m:
            continue
        pref = m.group(0)
        url = urllib.parse.urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        found.append({"pref": pref, "slug": PREF_SLUG[pref], "url": url, "label": label})
    return found


def build_one(entry: dict, outdir: Path, source_note: str, sleep_s: float,
              limit: int, no_geocode: bool) -> tuple[bool, str]:
    out = outdir / f"spots_{entry['slug']}.json"
    cmd = [
        sys.executable, str(HERE / "build_spots.py"),
        "--pdf", entry["url"],
        "--area", entry["slug"],
        "--pref", entry["pref"],
        "--out", str(out),
        "--sleep", str(sleep_s),
        "--source", f"{source_note} {entry['label']}".strip(),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    if no_geocode:
        cmd += ["--no-geocode"]

    print(f"\n{'=' * 60}\n  {entry['pref']}  →  {out.name}\n{'=' * 60}")
    print(f"  {entry['url']}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return False, f"取り込みに失敗（終了コード {proc.returncode}）"
    if not out.exists():
        return False, "出力ファイルが作られませんでした"
    n = len(json.loads(out.read_text(encoding="utf-8")).get("spots", []))
    return True, f"{n} 件"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, help="data/regions.json のキー（例: kanto）")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--list", action="store_true", help="見つかったPDFを一覧して終了")
    ap.add_argument("--only", default="", help="特定の都道府県だけ処理（例: 東京都）")
    ap.add_argument("--skip", default="", help="除外する都道府県をカンマ区切りで")
    ap.add_argument("--include-no-table", action="store_true",
                    help="一覧表が無いと記録済みの県も試す（資料の改訂を確かめるとき）")
    ap.add_argument("--sleep", type=float, default=1.0, help="ジオコーディングの間隔[秒]")
    ap.add_argument("--limit", type=int, default=0, help="各県の先頭N件だけ（動作確認用）")
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="1県でも取り込めなければ失敗扱いにする（既定は成功した県を活かす）")
    ap.add_argument("--html", default="", help="索引ページのHTMLをファイルから読む（試験用）")
    args = ap.parse_args()

    regions = load_regions()
    if args.region not in regions:
        print(f"未登録の地域です: {args.region}", file=sys.stderr)
        print(f"登録済み: {', '.join(regions)}", file=sys.stderr)
        return 1
    region = regions[args.region]

    html = (Path(args.html).read_text(encoding="utf-8") if args.html
            else fetch(region["index_url"]))
    entries = find_pdfs(html, region["index_url"])

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if args.only:
        entries = [e for e in entries if e["pref"] == args.only]

    # PDFが地図画像だけで一覧表が入っていない県は、毎回取りに行っても
    # 必ず0件になる。確認済みのものは regions.json に記録してあるので飛ばす。
    # 資料が改訂されて表が入るかもしれないので、--include-no-table で試せる。
    no_table = {k: v for k, v in (region.get("no_table") or {}).items()
                if not k.startswith("_")}
    if no_table and not args.include_no_table:
        hit = [e["pref"] for e in entries if e["pref"] in no_table]
        if hit:
            print(f"\n一覧表が無いため飛ばす県: {', '.join(hit)}")
            for pref in hit:
                print(f"  {pref}: {no_table[pref]}")
            print("  （--include-no-table で試せます。資料が改訂されていれば取り込めます）")
        entries = [e for e in entries if e["pref"] not in no_table]

    entries = [e for e in entries if e["pref"] not in skip]

    print(f"{region['name']}: PDF {len(entries)} 件")
    for e in entries:
        print(f"  {e['pref']:<6} {e['label']}")
        print(f"         {e['url']}")
    if args.list:
        return 0
    if not entries:
        print("対象がありません。索引ページの作りが変わった可能性があります。", file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    for e in entries:
        ok, msg = build_one(e, outdir, region.get("source_note", ""),
                            args.sleep, args.limit, args.no_geocode)
        results.append((e["pref"], ok, msg))

    print(f"\n{'=' * 60}\n  結果\n{'=' * 60}")
    total_ok = 0
    for pref, ok, msg in results:
        print(f"  {'OK' if ok else 'NG'}  {pref:<6} {msg}")
        total_ok += ok
    print(f"\n{total_ok}/{len(results)} 県を取り込みました")

    failed = [pref for pref, ok, _ in results if not ok]
    if failed:
        # 失敗した県があっても、成功した県の取り込みは活かす。
        # ここで止めると、9県中2県のPDFが違う作りだっただけで
        # 残り7県ぶん（20分かけた取得）を捨てることになる。
        # ただし黙って進むと欠けたことに気づけないので、必ず目立たせる。
        print(f"\n⚠ 取り込めなかった県: {', '.join(failed)}")
        print("   表の作りが違う可能性があります。--only で個別に "
              "--inspect / --dump-table して確かめてください。")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"\n### ⚠ 取り込めなかった県: {', '.join(failed)}\n")
    if args.strict and failed:
        return 1
    return 0 if total_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
