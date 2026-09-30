#!/usr/bin/env python3
"""公開Webページが裏で叩いているデータ取得口を調べる（docs/06 の調査手順 4）

千葉市 地下道冠水情報システム（https://pub.os-alert.info/chiba/devmap）のように、
地図上にリアルタイムの通行可否を出しているページは、裏で JSON などを取得している
ことが多い。それが機械可読なら取り込みを検討でき、無ければ自治体に連携を打診する
（→ docs/08_自治体連携_打診文案.md）という判断材料にする。

⚠️ スクレイピングの可否は各サイトの利用規約・robots.txt に従うこと。
   本スクリプトは「どうやってデータを出しているか」を1回調べるためのもので、
   継続的な自動取得を意図したものではない。

使い方:
    # 静的HTMLだけを見る（requests のみ・軽量）
    python3 tools/probe_endpoints.py --url https://pub.os-alert.info/chiba/devmap

    # ブラウザで実行して通信を記録する（要 playwright）
    python3 tools/probe_endpoints.py --url https://pub.os-alert.info/chiba/devmap --browser

    # 抽出ロジックだけをローカルHTMLで確認する
    python3 tools/probe_endpoints.py --html tools/fixtures/sample_devmap.html
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

# HTML/JS 中に現れる「データっぽい」URL
URL_RE = re.compile(r"""["'`](?P<url>(?:https?:)?//[^"'`\s]+|/[^"'`\s]{2,})["'`]""")
DATA_EXT = (".json", ".geojson", ".csv", ".xml", ".topojson", ".pbf", ".txt")
DATA_HINT = ("api", "data", "json", "geojson", "feature", "layer", "status",
             "kansui", "flood", "sensor", "devmap", "point")
STATIC_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ico", ".map")


def score_url(u: str) -> int:
    """データ取得口らしさのスコア。高いほど本命。"""
    low = u.lower()
    s = 0
    if any(low.split("?")[0].endswith(e) for e in DATA_EXT):
        s += 5
    if any(h in low for h in DATA_HINT):
        s += 2
    if any(low.split("?")[0].endswith(e) for e in STATIC_EXT):
        s -= 5
    return s


def extract_urls(html: str, base: str | None) -> list[str]:
    found = set()
    for m in URL_RE.finditer(html):
        u = m.group("url")
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            # base があれば絶対URL化する。無い場合（ローカルHTMLの確認）は相対のまま残す
            u = urljoin(base, u) if base else u
        found.add(u)
    return sorted(found)


def report(urls: list[str], title: str) -> None:
    ranked = sorted(((score_url(u), u) for u in urls), key=lambda t: (-t[0], t[1]))
    print(f"\n=== {title}（{len(urls)} 件） ===")
    hits = [(s, u) for s, u in ranked if s > 0]
    if hits:
        print("データ取得口の候補:")
        for s, u in hits[:25]:
            print(f"  [score {s:>2}] {u}")
    else:
        print("  データらしいURLは見つかりませんでした。")
        print("  → --browser で実際の通信を記録するか、ブラウザの開発者ツール(Network)で確認してください。")
    others = [u for s, u in ranked if s <= 0][:10]
    if others:
        print("\nその他（参考）:")
        for u in others:
            print(f"  {u}")


def probe_static(url: str) -> list[str]:
    import requests
    r = requests.get(url, timeout=30, headers={"User-Agent": "mizumichi-probe/0.1 (research)"})
    r.raise_for_status()
    print(f"HTTP {r.status_code} / {len(r.text)} 文字 / Content-Type: {r.headers.get('Content-Type')}")
    return extract_urls(r.text, url)


def probe_browser(url: str, wait_ms: int = 8000) -> list[str]:
    """ブラウザで開いて、実際に発生した通信を記録する。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright が必要です:  pip install playwright && playwright install chromium",
              file=sys.stderr)
        return []

    seen: list[tuple[str, str, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("response", lambda res: seen.append(
            (res.url, str(res.status), res.headers.get("content-type", ""))))
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(wait_ms)
        browser.close()

    print(f"\n記録した通信: {len(seen)} 件")
    print("\n--- JSON/テキストを返した通信（本命） ---")
    for u, st, ct in seen:
        if any(t in ct for t in ("json", "geo+json", "text/csv", "xml")):
            print(f"  [{st}] {ct:<32} {u}")
    return [u for u, _, _ in seen]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="調べる公開ページのURL")
    g.add_argument("--html", help="ローカルのHTMLファイル（抽出ロジックの確認用）")
    ap.add_argument("--browser", action="store_true", help="ブラウザで実行して通信を記録する")
    ap.add_argument("--json-out", default=None, help="候補URLをJSONで保存")
    args = ap.parse_args()

    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
        urls = extract_urls(html, None)
        report(urls, f"ローカルHTML {args.html} から抽出")
    else:
        host = urlparse(args.url).netloc
        print(f"調査対象: {args.url}")
        print("※ 利用規約・robots.txt を必ず確認してから実行してください。")
        urls = probe_browser(args.url) if args.browser else probe_static(args.url)
        report(urls, f"{host} から抽出")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(sorted(urls), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n保存: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
