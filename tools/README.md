# tools — データ整備・取込ツール

[docs/06_開発ロードマップ.md](../docs/06_開発ロードマップ.md) の「次にやること」を実行するためのスクリプト群。

> 手順を最初から追いたい場合は [はじめかた.md](../はじめかた.md) を見てください。
> 迷ったら `python3 tools/doctor.py` を打てば「次にやること」が出ます。

## セットアップ

```bash
pip install -r tools/requirements.txt
# プロトタイプの自動検証を使う場合のみ
npm i playwright && npx playwright install chromium
```

## 実行順

### ① プロトタイプを動かして確認する

```bash
cd prototype && python3 -m http.server 8000 &
open http://localhost:8000/          # 手で触る
node ../tools/verify_prototype.js    # 自動検証（14項目）
node ../tools/verify_ipad.js         # iPadの画面・タッチでの検証（14項目）
```

プロトタイプは MapLibre を CDN（unpkg）から読むので、外に出られない環境では
地図が出ず検証が止まる。その場合は同じ版を手元に置いて読ませる:

```bash
cd tools && npm i maplibre-gl@4.7.1     # prototype/index.html と同じ版
MAPLIBRE_DIR=$PWD/node_modules/maplibre-gl/dist node verify_prototype.js
```

`MAPLIBRE_DIR` を指定しなければ何もしないので、CDN に出られる環境（CI 含む）の
挙動は変わらない。アプリ側は書き換えないため、検証しているのは本番と同じコード。

### ①' いまの状態を診断する

```bash
python3 tools/doctor.py
```

Python・ライブラリ・node・外部サイトへの到達性・データの進み具合をまとめて確認し、
**次にやることを1つだけ提示**する。詰まったらまずこれ。

### ② 道路冠水注意箇所（千葉県内95箇所）を座標付きデータにする

```bash
# 1. PDFを入手（国交省 千葉国道事務所）
#    https://www.ktr.mlit.go.jp/chiba/chiba_index030.html

# 2. まず何が読めるかを確認する
python3 tools/build_spots.py --pdf 一覧表.pdf --dump-text | head -50

# 3. 抽出だけ試す（ジオコーディングなし）
python3 tools/build_spots.py --pdf 一覧表.pdf --no-geocode --out /tmp/dry.json

# 4. 座標を付けて出力（国土地理院の住所検索APIを使用・無料/キー不要）
python3 tools/build_spots.py --pdf 一覧表.pdf --area chiba \
    --source "国交省千葉国道事務所 道路冠水箇所マップ（2026-06-30版）" \
    --out data/spots_chiba.json

# 5. 標高タイルから相対標高 dz を付ける（この時点では目安。レビュー後にやり直す）
python3 tools/enrich_dem.py --in data/spots_chiba.json --out data/spots_chiba.json

# 6. OSMのトンネル情報へ座標を自動で寄せる（レビューの出発点を良くする）
python3 tools/osm_snap.py --spots data/spots_chiba.json --fetch
python3 tools/osm_snap.py --spots data/spots_chiba.json --dry-run   # 確認
python3 tools/osm_snap.py --spots data/spots_chiba.json --apply     # 反映

# 上限は名前の一致有無で分けている（既定: 一致300m / 不一致150m）。
# 「見つからない」を減らしたい場合は緩められるが、誤った位置に置く危険が増える
python3 tools/osm_snap.py --spots data/spots_chiba.json --dry-run --max-move-unnamed 250

# 7. ★人手レビュー：地図上で座標を確認・修正する（その場でJSONに保存される）
python3 tools/review_spots.py --spots data/spots_chiba.json

# 8. 座標が正しくなったので dz を計算し直す
python3 tools/enrich_dem.py --in data/spots_chiba.json --out data/spots_chiba.json

# 9. ★点検：公開・配布の前に必ず通す
python3 tools/validate_spots.py --spots data/spots_chiba.json

# 見つかった疑いをデータに書き戻す（レビュー画面の「要確認」に出る）
python3 tools/validate_spots.py --spots data/spots_chiba.json --annotate

# 10. アプリが読む形に書き出す
python3 tools/export_prototype_data.py --in data/spots_chiba.json \
    --out prototype/data/spots_chiba.json

# iPad / GitHub Codespaces から使う場合
python3 tools/review_spots.py --spots data/spots_chiba.json --ipad
```

`validate_spots.py` は、座標の範囲・重複・レビューの進み具合・閾値の分布・
危険度の出方をまとめて調べ、**対応が必要なものだけ**を挙げる。
「誤った地点を危険と表示する」ことを防ぐ最後の関門（docs/07）。

**dz は座標から計算するので、レビューの前後で2回実行する。** レビュー前の値は目安に過ぎず、
座標が丁目の中心にあるまま測った高低差には意味がない。
周囲100mとの高低差が ±6m を超える地点は「座標がずれている疑い」として警告する。

**レビューは省略しない。** 住所からの機械変換は丁目レベルで外れることがあり、
誤った地点を「危険」と表示するのは見逃しとは別種の害になる（docs/07）。
`review_spots.py` は要レビューの地点を順に出し、マーカーのドラッグで座標を直して
Enter で確定できる。航空写真に切り替えればアンダーパスかどうかを目視で確認できる。
変更は操作のたびに保存され、初回に `.bak` が作られる。

PDFのレイアウトは事務所ごとに違う。表として抽出できない場合は `--dump-text` の
出力を見て `build_spots.py` の `HEADER_MAP` / `LINE_RE` を調整する。

### ③ アメダスのリアルタイム雨量を取り込んで危険度を出す

```bash
# 通信あり（気象庁の実データ）
python3 tools/fetch_amedas.py --spots data/spots_chiba.json --out data/risk_latest.json

# 通信なし（フィクスチャで動作確認）
python3 tools/fetch_amedas.py --spots prototype/data/sample_spots.json \
    --fixture-dir tools/fixtures/amedas --out /tmp/risk.json
```

### ④ 千葉市 地下道冠水情報システムのデータ取得口を調べる

```bash
python3 tools/probe_endpoints.py --url https://pub.os-alert.info/chiba/devmap --browser
```

結果を踏まえて [docs/08_自治体連携_打診文案.md](../docs/08_自治体連携_打診文案.md) の文案を送る。

## 検証

```bash
./tools/run_tests.sh
```

すべてネットワーク不要（フィクスチャを使用）。内訳:

| テスト | 確認内容 |
|-------|---------|
| `test_risk_parity.py` | `risk.py` と `prototype/risk.js` が**全2,700ケースで一致**すること、docs/04 の計算例と合うこと |
| `test_build_spots.py` | PDFからの表抽出、種別推定、住所正規化、ジオコーディング結果の信頼度判定 |
| `test_enrich_dem.py` | 標高タイルのRGBデコード（往復・無効値）、相対標高 dz の算出、dz が閾値に効くこと |
| `test_fetch_amedas.py` | アメダスJSONの解釈（品質フラグ・[度,分]変換）、IDW内挿、危険度出力 |
| `test_review_spots.py` | レビュー画面のサーバ側（保存・バックアップ・進捗集計・異常系・再開） |
| `test_osm_snap.py` | 点と線分の距離、寄せ先の優先順位、上限、確認済みの保護、出典の記録 |
| `verify_prototype.js` | ブラウザでの実動作14項目（雨量フィルタ・現在地アラート・クールダウン・ラベル・ナウキャストの成功/失敗） |
| `verify_ipad.js` | iPad 横/縦でのはみ出し・タップ領域・主要操作 14項目 |
| `verify_live.js` | 「いまの雨量」モード（鮮度表示・切替・取り込み値の反映）8項目。要 `risk_latest.json` |
| `verify_support.js` | 検証スクリプトの共通処理（CDN に出られない環境で MapLibre を差し替える） |

## ファイル

| ファイル | 役割 |
|---------|------|
| `risk.py` | 危険度判定エンジン（サーバ側）。`prototype/risk.js` と同一の式 |
| `geo.py` | タイル座標変換、標高タイルのデコード、IDW内挿 |
| `build_spots.py` | ② 一覧PDF → 座標付き spots JSON + レビュー用CSV |
| `enrich_dem.py` | ② 標高タイルから相対標高 dz を付与 |
| `osm_snap.py` | ② OSMのトンネル情報へ座標を寄せる（住所検索では構造物を指せないため） |
| `validate_spots.py` | ② データの点検（座標・重複・レビュー状況・閾値と危険度の分布） |
| `export_prototype_data.py` | レビュー済みデータをアプリが読む形に書き出す（除外を省き、未確認に印を付ける） |
| `fetch_amedas.py` | ③ アメダス10分値 → 各地点の雨量と危険度 |
| `probe_endpoints.py` | ④ 公開ページのデータ取得口を調査 |
| `review_spots.py` / `.html` | ② 座標を地図上で確認・修正するレビュー画面（ローカルサーバ） |
| `doctor.py` | 環境診断と「次にやること」の提示 |
| `verify_prototype.js` | ① プロトタイプの自動検証 |
| `fixtures/` | ネットワーク不要のテスト用データ（すべてダミー） |

## ⚠️ 注意

- 気象庁のJSON・タイルは**公式APIとして仕様保証されたものではない**。
  スキーマが変わったら、誤った雨量で誤報を出さないよう**取り込みを止める**こと（docs/07）。
- `fixtures/` のデータはすべて**動作確認用のダミー**で、実在の観測値・危険箇所ではない。
- スクレイピングを行う前に、対象サイトの利用規約と robots.txt を確認すること。
