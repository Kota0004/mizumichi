#!/usr/bin/env python3
"""build_region.py の検証（ネットワーク不要）

索引ページから県別PDFを拾う部分を、実物と同じ作りのHTMLで確かめる。
リンクテキストは Actions 上で実際に取得したものをそのまま使っている。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_region as B  # noqa: E402

ok, ng = [], []


def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


# 実物の関東地方整備局ページと同じ作り。
# 政令市が続く書き方（神奈川県・川崎市…）や、PDF以外のリンクを混ぜてある。
KANTO_HTML = """
<html><body>
<h1>関東甲信地域における道路冠水注意箇所マップ</h1>
<ul>
  <li><a href="/road/bousai/index.html">道路防災情報トップ</a></li>
  <li><a href="/ktr_content/content/000015506.pdf">茨城県 令和8年6月1日【更新】[PDF:1.2MB]</a></li>
  <li><a href="/ktr_content/content/000681500.pdf">栃木県 令和8年6月1日【更新】[PDF:2.1MB]</a></li>
  <li><a href="/ktr_content/content/000708201.pdf">群馬県 令和8年6月15日【更新】[PDF:498KB]</a></li>
  <li><a href="/ktr_content/content/000708920.pdf">埼玉県・さいたま市 令和8年6月10日【更新】[PDF:2.3MB]</a></li>
  <li><a href="/ktr_content/content/000752021.pdf">千葉県・千葉市 令和8年6月30日【更新】[PDF:1018KB]</a></li>
  <li><a href="/ktr_content/content/000752436.pdf">東京都 令和8年6月15日【更新】[PDF:3.5MB]</a></li>
  <li><a href="/ktr_content/content/000920809.pdf">神奈川県・川崎市・横浜市・相模原市 令和8年6月1日【更新】[PDF:716KB]</a></li>
  <li><a href="/ktr_content/content/000708204.pdf">山梨県 令和8年6月15日【更新】[PDF:469KB]</a></li>
  <li><a href="/ktr_content/content/000718050.pdf">長野県 令和8年5月【更新】[PDF:1.5MB]</a></li>
  <li><a href="/ktr_content/content/000999999.pdf">道路防災の取り組みについて [PDF:100KB]</a></li>
</ul>
</body></html>
"""
BASE = "https://www.ktr.mlit.go.jp/road/bousai/road_bousai00000001.html"

found = B.find_pdfs(KANTO_HTML, BASE)
prefs = [e["pref"] for e in found]

check("関東の9県ぶんを拾う", len(found) == 9, f"{len(found)} 件: {prefs}")
check("千葉が含まれる", "千葉県" in prefs)
check("東京が含まれる", "東京都" in prefs)

# 県名を含まないPDF（「道路防災の取り組みについて」）を拾ってしまうと、
# 一覧表でないPDFを解析して誤ったデータを作ってしまう。
check("県名のないPDFは拾わない", len(found) == 9 and all(p in B.PREF_SLUG for p in prefs))

# 政令市が続いても県名だけを取れているか。ここを間違えると
# --pref に渡す値が壊れ、住所の組み立てが狂う。
kanagawa = next((e for e in found if e["pref"] == "神奈川県"), None)
check("政令市が続いても県名だけを取る", kanagawa is not None and kanagawa["slug"] == "kanagawa",
      kanagawa["label"][:24] if kanagawa else "見つからない")
saitama = next((e for e in found if e["pref"] == "埼玉県"), None)
check("埼玉県・さいたま市 も県名だけ", saitama is not None and saitama["slug"] == "saitama")

# 相対パスが絶対URLになるか
chiba = next((e for e in found if e["pref"] == "千葉県"), None)
check("相対パスを絶対URLにする",
      chiba is not None and chiba["url"] ==
      "https://www.ktr.mlit.go.jp/ktr_content/content/000752021.pdf",
      chiba["url"] if chiba else "")

# 同じPDFが2か所から貼られていても1件にする
dup = B.find_pdfs(KANTO_HTML + KANTO_HTML, BASE)
check("同じPDFは1件にまとめる", len(dup) == 9, f"{len(dup)} 件")

# PDFが1つも無いページ（索引の作りが変わった場合）は空で返す。
# ここで例外を投げると原因が分かりにくくなる。
check("PDFが無ければ空で返す", B.find_pdfs("<html><body>準備中</body></html>", BASE) == [])

# 一覧表が無い県は既定で飛ばす。
# 山梨・長野のPDFは地図画像だけで表が入っておらず、毎回取りに行っても必ず0件になる。
# パーサの問題ではないので、記録して飛ばす。
regions_all = B.load_regions()
no_table = {k: v for k, v in (regions_all["kanto"].get("no_table") or {}).items()
            if not k.startswith("_")}
check("一覧表が無い県が記録されている", set(no_table) == {"山梨県", "長野県"},
      str(sorted(no_table)))
check("なぜ取り込めないかを書いてある",
      all(len(v) > 10 for v in no_table.values()),
      str(list(no_table.values())[:1]))
check("記録した県は prefs に入れない",
      not (set(no_table) & set(regions_all["kanto"]["prefs"])),
      str(regions_all["kanto"]["prefs"]))

# regions.json が読めて、kanto が ready になっているか
regions = B.load_regions()
check("regions.json を読める", "kanto" in regions, f"{list(regions)}")
check("kanto は取り込み可の印がついている",
      regions.get("kanto", {}).get("status") == "ready")
check("登録した県がすべて PREF_SLUG にある",
      all(p in B.PREF_SLUG for r in regions.values() if isinstance(r, dict)
          for p in r.get("prefs", [])))

print("\n===== build_region の検証 =====")
for s in ok:
    print("  OK " + s)
for s in ng:
    print("  NG " + s)
print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
raise SystemExit(1 if ng else 0)
