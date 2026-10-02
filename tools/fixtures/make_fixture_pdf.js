/* テスト用の「道路冠水注意箇所一覧表」PDFを生成する。
   実物のPDF（国交省）は再配布条件の確認が必要なため、リポジトリには置かず、
   同じ構造のダミーPDFを自前で作って build_spots.py の検証に使う。

   使い方:  node tools/fixtures/make_fixture_pdf.js
   （要 playwright: npm i -D playwright） */
const { chromium } = require('playwright');
const path = require('path');

const ROWS = [
  ['1',  '国道○号',   'サンプル地下道A',   '千葉市中央区サンプル町1-1',   'アンダーパス',          '国交省'],
  ['2',  '国道○号',   'サンプル地下道B',   '千葉市花見川区サンプル2-3',   'アンダーパス（ポンプ有）', '国交省'],
  ['3',  '県道○号',   'サンプル橋詰め',     '船橋市サンプル3-4',           '橋詰め',                '千葉県'],
  ['4',  '市道',       'サンプル低地交差点', '市川市サンプル5-6',           '低地',                  '市川市'],
  ['5',  '国道○号',   'サンプルガード下',   '松戸市サンプル7-8',           'ガード下',              '国交省'],
  ['6',  '県道○号',   'サンプル河川沿い',   '柏市サンプル9-10',            '河川隣接',              '千葉県'],
  ['7',  '市道',       'サンプル窪地',       '習志野市サンプル11-12',       'くぼ地',                '習志野市'],
  ['8',  '国道○号',   'サンプル立体交差',   '木更津市サンプル13-14',       '立体交差',              '国交省'],
];

const html = `<!doctype html><html lang="ja"><meta charset="utf-8">
<style>
  body{font-family:'Noto Sans CJK JP','Noto Sans JP',sans-serif;font-size:10pt;margin:18mm}
  h1{font-size:13pt;margin:0 0 4mm}
  .sub{font-size:9pt;color:#333;margin:0 0 6mm}
  table{border-collapse:collapse;width:100%}
  th,td{border:1px solid #333;padding:3px 6px;font-size:9pt}
  th{background:#eee}
</style>
<h1>道路冠水注意箇所一覧表（テスト用ダミー）</h1>
<p class="sub">※ build_spots.py の動作確認のために生成した架空のデータです。実在の箇所ではありません。</p>
<table>
  <tr><th>番号</th><th>路線名</th><th>箇所名</th><th>所在地</th><th>種別</th><th>管理者</th></tr>
  ${ROWS.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('\n  ')}
</table>
</html>`;

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined
  });
  const page = await browser.newPage();
  await page.setContent(html, { waitUntil: 'load' });
  const out = path.join(__dirname, 'sample_kansui_list.pdf');
  await page.pdf({ path: out, format: 'A4', printBackground: true });
  await browser.close();
  console.log('生成:', out);
})();
