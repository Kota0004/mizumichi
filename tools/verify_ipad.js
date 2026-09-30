/* iPad（タッチ・画面サイズ）でプロトタイプが使えるかの検証
 *
 *   1) cd prototype && python3 -m http.server 8000
 *   2) node ../tools/verify_ipad.js
 *
 * iPad 横(1180x820) / 縦(820x1180) の両方で、はみ出し・タップ領域・
 * 主要操作（プリセット・現在地アラート）を確認する。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre, positionNearAlertableSpot } = require('./verify_support');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const ok = [], ng = [];
const check = (n,c,e='') => (c?ok:ng).push(n + (e?` — ${e}`:''));
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  for (const [label, vp] of [['iPad 横', {width:1180,height:820}], ['iPad 縦', {width:820,height:1180}]]) {
    const ctx = await b.newContext({ viewport: vp, hasTouch: true, deviceScaleFactor: 2,
      permissions:['geolocation'], geolocation:{longitude:140.1080, latitude:35.6120} });
    const p = await ctx.newPage();
    await useLocalMaplibre(p);
    const errs = []; p.on('pageerror', e => errs.push(e.message));
    await p.goto(`${BASE}/index.html`, { waitUntil:'load' });
    await p.waitForSelector('.cnt'); await p.waitForTimeout(700);
    // 現在地はデータから決める（座標の決め打ちは取り込み直しで壊れる）
    const home = await positionNearAlertableSpot(p, 200);
    await ctx.setGeolocation({ longitude: home.longitude, latitude: home.latitude });

    check(`${label}: 横スクロールなし`, await p.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));
    // パネルが画面内に収まるか
    const fits = await p.evaluate(() => {
      const r = document.querySelector('.panel').getBoundingClientRect();
      return r.bottom <= window.innerHeight + 1 && r.right <= window.innerWidth + 1;
    });
    check(`${label}: 操作パネルが画面内に収まる`, fits);
    // スライダーをタッチで動かせるか
    const slider = await p.$('#rain');
    const sb = await slider.boundingBox();
    check(`${label}: スライダーのタップ領域が十分`, sb.height >= 20, `高さ=${Math.round(sb.height)}px`);
    await p.tap('.presets button[data-r="100"]');
    await p.waitForTimeout(400);
    check(`${label}: プリセットをタップで100mm/hになる`,
          (await p.textContent('#rainNum')) === '100', await p.textContent('#rainNum'));
    // 現在地アラート
    await p.tap('#geoBtn'); await p.waitForTimeout(1500);
    check(`${label}: 現在地アラートが出る`,
          await p.evaluate(() => document.getElementById('alert').classList.contains('show')));
    const alertFits = await p.evaluate(() => {
      const r = document.getElementById('alert').getBoundingClientRect();
      return r.left >= -1 && r.right <= window.innerWidth + 1;
    });
    check(`${label}: アラートが画面からはみ出さない`, alertFits);
    check(`${label}: JSエラーなし`, errs.length === 0, errs.slice(0,1).join(''));
    if (process.env.SHOTS) await p.screenshot({ path: `ipad_${vp.width}x${vp.height}.png` });
    await ctx.close();
  }
  console.log('\n===== プロトタイプ iPad 検証 =====');
  ok.forEach(s => console.log('  ✅ ' + s));
  ng.forEach(s => console.log('  ❌ ' + s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);
  await b.close(); process.exit(ng.length?1:0);
})();
