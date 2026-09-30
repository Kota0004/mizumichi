/* 雨量が取れていない地点を「平常（安全）」と表示しないことの検証
 *
 * アメダスの観測点から遠い地点は内挿できない。実運用で827件中108件が
 * これに当たった。冠水アプリで「分からない」を「安全」と表示するのは
 * 最も避けるべき壊れ方なので、ここが戻ったら気づけるよう固定しておく。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre } = require('./verify_support');
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const ok = [], ng = [];
const check = (n, c, e = '') => (c ? ok : ng).push(n + (e ? ` — ${e}` : ''));

const SPOTS = {
  version: '1.0.0', generated_at: '2026-09-17', area: '試験',
  counts: { total: 2, verified: 0, unverified: 2, point: 2, area: 0 },
  attribution: ['試験用'], disclaimer: '試験用',
  spots: [
    { id: 'x-0001', name: '雨量あり', kind: 'underpass_gravity',
      lon: 140.1080, lat: 35.6121, dz: -3, hist: 0, t60: 21,
      road: '1号', address: '試験県', verified: false, precision: 'point' },
    { id: 'x-0002', name: '雨量なし', kind: 'underpass_gravity',
      lon: 140.1085, lat: 35.6122, dz: -3, hist: 0, t60: 21,
      road: '1号', address: '試験県', verified: false, precision: 'point' },
  ],
};
// x-0002 はわざと入れない（観測点が遠くて内挿できなかった状態）
const LIVE = {
  generated_at: new Date().toISOString(),
  source_freshness: { amedas: new Date().toISOString() },
  spots: { 'x-0001': { level: 3, score: 61, t60: 21,
                       rain: { r10: 5, r60: 30, r180: 60 },
                       reason: '時間雨量 30mm（この地点の閾値 21mm）' } },
  _stats: { spots: 1, missing: 1 },
};

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 }, permissions: ['geolocation'],
    geolocation: { longitude: 140.1085, latitude: 35.6118 }, locale: 'ja-JP' });
  const page = await ctx.newPage();
  await useLocalMaplibre(page);
  for (const u of ['**/data/spots.json', '**/data/spots_chiba.json']) {
    await page.route(u, r => r.fulfill({ status: 200, contentType: 'application/json',
                                         body: JSON.stringify(SPOTS) }));
  }
  await page.route('**/data/risk_latest.json', r => r.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(LIVE) }));

  await page.goto(`${BASE}/index.html`, { waitUntil: 'load' });
  await page.waitForSelector('.cnt', { timeout: 20000 });
  await page.waitForTimeout(1200);

  // 「いまの雨量」に切り替える
  await page.click('#liveBtn');
  await page.waitForTimeout(900);

  const state = await page.evaluate(() => {
    const cnt = {};
    document.querySelectorAll('.cnt').forEach(e => { cnt[e.dataset.lv] = e.textContent.trim(); });
    const feats = window.mizumichiMap.querySourceFeatures
      ? null : null;
    return { cnt, legend: document.getElementById('legend').textContent.replace(/\s+/g, ' ') };
  });

  check('凡例に「不明」がある', /不明/.test(state.legend), state.legend.slice(0, 120));
  check('雨量なしは不明として数える', state.cnt.unknown === '1 件',
        `不明=${state.cnt.unknown}`);
  check('雨量なしを平常に数えない', state.cnt['0'] === '0 件',
        `平常=${state.cnt['0']}`);
  check('雨量ありは通常どおり数える', state.cnt['3'] === '1 件', `レベル3=${state.cnt['3']}`);

  // 合計は全件ぶんになる（不明を数え落としていない）
  const total = Object.values(state.cnt)
    .reduce((a, v) => a + parseInt(v), 0);
  check('集計の合計が全件ぶん', total === 2, `${total} 件`);

  // 雨量が分からない地点では鳴らさない
  await page.click('#geoBtn');
  await page.waitForTimeout(2500);
  const alertText = await page.evaluate(() => {
    const el = document.getElementById('alert');
    return el.classList.contains('show') ? el.textContent.replace(/\s+/g, ' ') : '';
  });
  check('雨量が分かる地点でだけ鳴る', /雨量あり/.test(alertText) && !/雨量なし/.test(alertText),
        alertText.slice(0, 80) || '鳴っていない');

  console.log('\n===== 雨量が取れていない地点の扱い =====');
  ok.forEach(s => console.log('  OK ' + s));
  ng.forEach(s => console.log('  NG ' + s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);
  await browser.close();
  process.exit(ng.length ? 1 : 0);
})();
