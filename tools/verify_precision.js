/* 位置が推定でしかない地点で、現在地アラートを鳴らさないことの検証
 *
 * 資料に番地が無い県（東京都など）では座標が市区町村の中心付近になり、
 * 実際の構造物と数km離れる。そこで警報を鳴らすと、本当に危ない場所で鳴らず
 * 無関係な場所で鳴るので、地点を持たないより悪い。
 * ここが壊れたら気づけるように固定しておく。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre } = require('./verify_support');
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const ok = [], ng = [];
const check = (n, c, e = '') => (c ? ok : ng).push(n + (e ? ` — ${e}` : ''));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });

  // 推定位置の地点だけを、現在地のすぐ隣に置いたデータで試す
  const AREA_ONLY = {
    version: '1.0.0', generated_at: '2026-09-17', area: '試験',
    counts: { total: 1, verified: 0, unverified: 1, point: 0, area: 1 },
    attribution: ['試験用'], disclaimer: '試験用',
    spots: [{
      id: 'x-0001', name: '推定アンダー', kind: 'underpass_gravity',
      lon: 140.1080, lat: 35.6121, dz: -3.0, hist: 0, t60: 21,
      road: '1号', address: '試験都試験区', verified: false,
      precision: 'area', precision_note: '資料の住所が市区町村までしかない',
    }],
  };
  const POINT = JSON.parse(JSON.stringify(AREA_ONLY));
  POINT.spots[0].precision = 'point';
  POINT.counts = { total: 1, verified: 0, unverified: 1, point: 1, area: 0 };

  async function run(data) {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 }, permissions: ['geolocation'],
      geolocation: { longitude: 140.1080, latitude: 35.6120 }, locale: 'ja-JP',
    });
    const page = await ctx.newPage();
    await useLocalMaplibre(page);
    // アプリは spots.json を先に読むので、そちらを差し替える。
    // spots_chiba.json だけ差し替えても実データが読まれてしまう。
    await page.route('**/data/spots.json', r => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(data) }));
    await page.route('**/data/spots_chiba.json', r => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(data) }));
    await page.goto(`${BASE}/index.html`, { waitUntil: 'load' });
    await page.waitForSelector('.cnt', { timeout: 20000 });
    await page.evaluate(() => {
      const el = document.getElementById('rain'); el.value = 120;
      el.dispatchEvent(new Event('input'));
    });
    await page.click('#geoBtn');
    await page.waitForTimeout(2500);
    const shown = await page.evaluate(() =>
      document.getElementById('alert').classList.contains('show'));
    const html = await page.content();
    await ctx.close();
    return { shown, html };
  }

  const areaRes = await run(AREA_ONLY);
  check('位置が推定の地点では現在地アラートを鳴らさない', areaRes.shown === false,
        `アラート表示=${areaRes.shown}`);
  check('推定であることを画面に出す', /位置は推定/.test(areaRes.html));

  // 同じ条件で precision を point にすると鳴る＝
  // 「たまたま鳴らなかった」のではないことを確かめる
  const pointRes = await run(POINT);
  check('同じ条件でも地点品質なら鳴る（対照）', pointRes.shown === true,
        `アラート表示=${pointRes.shown}`);

  console.log('\n===== 位置の確からしさによる出し分け =====');
  ok.forEach(s => console.log('  OK ' + s));
  ng.forEach(s => console.log('  NG ' + s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);
  await browser.close();
  process.exit(ng.length ? 1 : 0);
})();
