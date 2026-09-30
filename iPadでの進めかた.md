# iPadで開発する方法

**結論: できます。** ただし iPad には Python も git も入っていないので、
「iPadの中で動かす」のではなく **「クラウド上の開発環境を Safari から操作する」** のが正解です。

方法は3つ。**おすすめは A（GitHub Codespaces）**です。

| | 方法 | できること | 費用 | 難易度 |
|---|------|-----------|------|-------|
| **A** | **GitHub Codespaces** ⭐ | **全部**（ターミナル・Python・地図画面・レビュー） | 無料枠 月60時間 | ★★ |
| **B** | このチャット（Claude Code on the web） | コマンドは私が実行。PDFの受け渡しはGitHub経由 | いま使っている分 | ★ |
| **C** | GitHub Pages | プロトタイプを**触るだけ** | 無料 | ★ |

> 📱 **共通のコツ**: 外付けキーボード（Magic Keyboard等）があると別物のように快適になります。
> なくても、A・B・Cすべて画面タッチだけで完結できるようにしてあります。

---

## A. GitHub Codespaces（おすすめ）

Microsoftのクラウド上にLinuxのパソコンが立ち上がり、**Safariの中にVS Code**が出ます。
ターミナルも使えるので、`はじめかた.md` の手順が**そのまま全部**実行できます。

このリポジトリには設定ファイル（`.devcontainer/devcontainer.json`）を入れてあるので、
**必要なライブラリは自動で入ります**。`pip install` を打つ必要はありません。

### A-1. 起動する（初回のみ3〜5分）

1. Safari で <https://github.com/Kota0004/mizumichi> を開く
2. 緑の **「Code」** ボタン → **「Codespaces」** タブ → **「Create codespace on main」**
   - Codespace は「いま見ているブランチ」に対して作られる。既定ブランチ（`main`）に
     設定ファイル（`.devcontainer/`）があるので、そのまま作ればライブラリが自動で入る
3. 画面下の「ターミナル」が使えるようになったら準備完了

> ⚠️ 初回は環境の構築に3〜5分かかります。`postCreateCommand` が終わるまで待ってください。
> ターミナルが見当たらないときは、左上の **☰ メニュー → Terminal → New Terminal**。
> 外付けキーボードがあれば **Ctrl + `**（バッククォート）でも開きます。

### A-2. 動作確認

ターミナルに次を打ちます。

```bash
python3 tools/doctor.py
```

Codespacesは外部への通信が自由なので、**気象庁も国土地理院も ✅ になるはず**です。
（いまのチャットの実行環境は通信が制限されていて ❌ になります）

### A-3. プロトタイプを触る

```bash
cd prototype && python3 -m http.server 8000
```

画面下の **「ポート」タブ** に `8000` が現れるので、その行の
**地球儀アイコン（ブラウザで開く）** をタップ。新しいSafariのタブでプロトタイプが開きます。

### A-4. 座標レビュー（ステップ3）

```bash
cd /workspaces/mizumichi
python3 tools/review_spots.py --spots data/spots_chiba.json --ipad
```

`--ipad` を付けると、Codespacesから開けるように待ち受けます。
**「ポート」タブの 8765** を開いてください。

**iPadでの操作**（タッチ用に作ってあります）:

| やること | 操作 |
|---------|------|
| 一覧を出す | 左上の **☰ 一覧** をタップ |
| 位置を直す | 地図を指で動かして中心を合わせ、**［📍 画面の中心をこの地点にする］** |
| （細かく直す） | 青いマーカーを直接ドラッグ、または地図をタップ |
| 確定して次へ | **［✔ この位置でOK］** |
| アンダーパス確認 | 右上の **「写真」** で航空写真に切り替え |

### A-5. PDFを取り込む（ステップ2）

Safariで国交省のページからPDFをダウンロードし、**「ファイル」アプリ**に保存 →
Codespacesのファイル一覧（左側）に**ドラッグ＆ドロップ**するとアップロードできます。

うまくいかない場合は、Codespacesのターミナルで直接ダウンロードするのが確実です。

```bash
curl -o 一覧表.pdf "PDFのURLをここに貼る"
```

> PDFのURLは、Safariでリンクを長押し →「リンクをコピー」で取れます。
> **貼り付けるのはURLだけ**です。`< >` や `＜ ＞` で囲むと
> `URL rejected: Port number was not a decimal number` というエラーになります。

### A-6. 保存して終わる

```bash
git add -A && git commit -m "作業内容" && git push
```

Codespacesは**使っていないと自動で停止**します（課金も止まります）。
再開すると続きから使えます。使い終わったCodespaceは
<https://github.com/codespaces> から削除できます。

---

## B. このチャットに任せる（いまのやり方の続き）

コマンドは私が実行します。あなたは日本語で指示するだけです。
ただし**いまの実行環境は外部サイトへの通信が遮断されている**ため、
気象庁や国交省から直接データを取れません。そこで:

### PDFの渡しかた（Safariだけで完結）

1. 国交省のページからPDFをダウンロード（「ファイル」アプリに保存される）
2. Safariで <https://github.com/Kota0004/mizumichi> を開く
3. **「Add file」→「Upload files」** をタップ
4. 「ファイル」アプリからPDFを選ぶ → 下の **Commit changes**
5. チャットで「PDFを上げたので処理して」と言う → 私が取り込んで座標化します

### レビュー結果の見かた

私が処理した結果はリポジトリにプッシュするので、
GitHubのWeb UI（Safari）で中身を確認できます。
ただし**地図での座標レビューはこの方法だとできません**（Aが必要です）。

---

## C. プロトタイプを触るだけなら GitHub Pages

「まず動くものを見たいだけ」なら、これが一番速いです。
`.github/workflows/pages.yml` が毎時 `prototype/` を公開しているので、次のURLを開くだけです。

```
https://kota0004.github.io/mizumichi/
```

Safariのブックマークに入れておけば、いつでも手元で触れます。位置情報も使えます
（Pages は HTTPS なので、iPadの位置情報の許可が出ます）。

> 公開が止まっているときは <https://github.com/Kota0004/mizumichi/settings/pages> を開き、
> **Build and deployment** の **Source** が **GitHub Actions** になっているかを確かめてください。

---

## やめておいたほうがいい方法

| 方法 | 理由 |
|------|------|
| **a-Shell / iSH（iOS上のターミナルアプリ）** | Python は動きますが、`pdfplumber` や `Pillow` は内部でC言語のコンパイルが必要で、iPad上ではまず入りません。ハマるだけです |
| **Working Copy 単体** | gitの操作（コミット・閲覧）はとても優秀ですが、Pythonは動きません。A・Bと併用するなら便利です |
| **Swift Playgrounds** | このプロジェクトはPython/Webなので用途が合いません |

---

## どれを選ぶか

```
とりあえず動くものを見たい        → C（3分）
本気で開発を進めたい             → A（推奨）
コマンドを打ちたくない            → B（このチャットの続き）
```

**A と B の併用が一番ラクです。**
考える・書く・調べるはこのチャットで私にやらせて、
実データを触るときだけ Codespaces を開く、という使い分けができます。

迷ったら Codespaces のターミナルで:

```bash
python3 tools/doctor.py
```

いまの状態と次にやることが出ます。
