/* 公開されたサイトが本当に動いているかを確かめる（Playwright）
 *
 *   BASE_URL=https://kota0004.github.io/mizumichi node verify_deployed.js
 *
 * デプロイが「成功」と報告されても、それは GitHub が受理したという意味でしかない。
 * 実際にページが開いて、危険箇所が読み込まれて、地図が出るところまでを見る。
 *
 * 外部のタイル配信が一時的に不調でも落とさないよう、地図は「生成されたか」だけを
 * 見て、タイル画像そのものの取得は判定に入れない。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre } = require('./verify_support');
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const ok = [], ng = [];
const check = (name, cond, extra = '') => (cond ? ok : ng).push(name + (extra ? ` — ${extra}` : ''));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await useLocalMaplibre(page);

  const jsErrors = [];
  page.on('pageerror', e => jsErrors.push(e.message));
  page.on('console', m => {
    const t = m.text();
    if (m.type() === 'error' && !/ERR_TUNNEL|Failed to fetch|Failed to load resource/.test(t)) jsErrors.push(t);
  });

  const res = await page.goto(`${BASE}/index.html`, { waitUntil: 'load', timeout: 45000 });
  check('ページが200で返る', res && res.status() === 200, `HTTP ${res && res.status()}`);

  await page.waitForSelector('.cnt', { timeout: 30000 });
  await page.waitForTimeout(1500);

  const data = await page.evaluate(() => {
    const d = window.mizumichiData || {};
    const m = d.meta || {};
    return { n: (d.spots || []).length, area: m.area, real: m.real === true,
             counts: m.counts || {}, cite: (m.attribution || []).length };
  });
  check('危険箇所が読み込まれている', data.n > 0, `${data.n} 件 / ${data.area || '?'}`);
  check('ダミーではなく実データ', data.real, `確認済み ${data.counts.verified} / 未確認 ${data.counts.unverified}`);
  check('出典が表示できる状態にある', data.cite > 0, `${data.cite} 件`);

  check('分類の集計が全件ぶん出ている',
        (await page.$$eval('.cnt', e => e.map(x => parseInt(x.textContent))))
          .reduce((a, b) => a + b, 0) === data.n);

  check('地図と危険箇所レイヤが生成されている', await page.evaluate(() =>
    !!(window.mizumichiMap && window.mizumichiMap.getLayer('spots-halo')
       && window.mizumichiMap.getLayer('spots-dot'))));

  // 毎時の取り込みが届いているか。まだ一度も走っていない場合もあるので、
  // 「ファイルが無い」ことは失敗にせず、あるなら中身が妥当かを見る。
  const live = await page.evaluate(async (base) => {
    try {
      const r = await fetch(base + '/data/risk_latest.json', { cache: 'no-store' });
      if (!r.ok) return { present: false, status: r.status };
      const j = await r.json();
      return { present: true, spots: Object.keys(j.spots || {}).length, at: j.generated_at };
    } catch (e) { return { present: false, error: e.message }; }
  }, BASE);
  if (live.present) {
    check('取り込み済みの雨量が届いている', live.spots > 0, `${live.spots} 件 / ${live.at}`);
  } else {
    ok.push(`雨量はまだ未取込（${live.status || live.error}）— 初回の取り込み前なら正常`);
  }

  check('JSエラーが出ていない', jsErrors.length === 0, jsErrors.slice(0, 3).join(' | '));

  console.log(`\n===== 公開サイトの確認: ${BASE} =====`);
  ok.forEach(s => console.log('  OK ' + s));
  ng.forEach(s => console.log('  NG ' + s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);

  await browser.close();
  process.exit(ng.length ? 1 : 0);
})();
