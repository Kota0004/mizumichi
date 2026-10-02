/* プロトタイプの動作検証（Playwright）
 *
 *   1) cd prototype && python3 -m http.server 8000
 *   2) cd tools && npm i playwright && npx playwright install chromium
 *   3) node verify_prototype.js            （別のURLなら BASE_URL=... node verify_prototype.js）
 *
 * 位置情報は Playwright 側で許可・座標を固定するため、実機の許可ダイアログは不要。
 * 地図タイルの取得失敗（ネットワーク由来）はエラーとして数えない。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre, positionNearAlertableSpot } = require('./verify_support');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const ok = [], ng = [];
const check = (name, cond, extra='') => (cond ? ok : ng).push(name + (extra ? ` — ${extra}` : ''));

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined
  });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 860 },
    permissions: ['geolocation'],
    geolocation: { longitude: 140.1080, latitude: 35.6120 },   // 読み込み後にデータから決め直す
    locale: 'ja-JP'
  });
  const page = await ctx.newPage();

  await useLocalMaplibre(page);

  const jsErrors = [];
  page.on('pageerror', e => jsErrors.push(e.message));
  page.on('console', m => {
    const t = m.text();
    if (m.type() === 'error' && !/ERR_TUNNEL|Failed to fetch|Failed to load resource/.test(t)) jsErrors.push(t);
  });

  await page.goto(`${BASE}/index.html`, { waitUntil: 'load' });
  await page.waitForSelector('.cnt', { timeout: 10000 });
  await page.waitForTimeout(600);

  // 現在地は、実際にアラート対象にできる地点の200m手前に置く。
  // 座標を決め打ちにすると、取り込み直しで地点が少し動いただけで落ちる。
  const home = await positionNearAlertableSpot(page, 200);
  await ctx.setGeolocation({ longitude: home.longitude, latitude: home.latitude });
  console.log(`現在地の基準: ${home.spot.id} ${home.spot.name} の200m手前`);

  const counts = () => page.$$eval('.cnt', els => els.map(e => parseInt(e.textContent)));
  // 件数はデータ次第（サンプル14件／実データ89件）なので、アプリ自身から取る
  const TOTAL = await page.evaluate(() => window.mizumichiData.spots.length);
  console.log(`読み込まれた地点数: ${TOTAL} 件`);

  // --- T1: 初期表示 ---
  const c0 = await counts();
  check('T1 初期表示（50mm/h）で全件が分類される', c0.reduce((a,b)=>a+b,0) === TOTAL,
        `内訳=${c0.join('/')} 合計=${TOTAL}`);

  // --- T2: 雨量スライダーが単調に危険度を上げる ---
  const series = {};
  for (const r of [0, 20, 30, 50, 80, 120]) {
    await page.evaluate(v => { const el=document.getElementById('rain'); el.value=v; el.dispatchEvent(new Event('input')); }, r);
    await page.waitForTimeout(200);
    const c = await counts();
    // 加重平均レベル
    series[r] = c.reduce((a,n,i)=>a+n*i,0) / 14;
  }
  const vals = Object.values(series);
  const monotone = vals.every((v,i) => i===0 || v >= vals[i-1]);
  check('T2 雨量を上げると平均危険度が単調に上がる', monotone,
        Object.entries(series).map(([k,v])=>`${k}mm:${v.toFixed(2)}`).join(' '));

  // --- T3: 同じ雨量で地点により判定が分かれる（設計の肝） ---
  await page.evaluate(() => { const el=document.getElementById('rain'); el.value=50; el.dispatchEvent(new Event('input')); });
  await page.waitForTimeout(200);
  const spread = await page.evaluate(() => {
    const M = window.MizuMichi;
    const lv = window.mizumichiData.spots.map(s =>
      M.score(Object.assign({}, s, { t60: s.t60 || M.computeT60(s) }), { r60: 50 }).level);
    return { min: Math.min(...lv), max: Math.max(...lv), n: lv.length };
  });
  check('T3 50mm/h で地点により判定が分かれる', spread.max - spread.min >= 1,
        `最小Lv${spread.min}〜最大Lv${spread.max}（${spread.n}件）`);

  // --- T4: レベルフィルタは「地図の表示」だけを絞り、集計は全件のまま ---
  await page.selectOption('#minLv', '4');
  await page.waitForTimeout(300);
  const c4 = await counts();
  check('T4 レベルフィルタを変えても集計は全件のまま（表示だけ絞られる）',
        c4.reduce((a,b)=>a+b,0) === TOTAL, `内訳=${c4.join('/')}`);
  await page.selectOption('#minLv', '2');
  await page.waitForTimeout(300);

  // --- T5: 実際の Geolocation API でアラートが出る ---
  await page.click('#geoBtn');
  await page.waitForTimeout(1500);
  const alertOn = await page.evaluate(() => document.getElementById('alert').classList.contains('show'));
  const alertTtl = alertOn ? await page.textContent('#alertTtl') : '';
  const alertLv  = alertOn ? await page.textContent('#alertLv') : '';
  check('T5 位置情報許可でアラートが自動発火する', alertOn, `${alertLv.trim()} / ${alertTtl.trim()}`);
  const btnLabel = await page.textContent('#geoBtn');
  check('T5b 監視ボタンが「停止」に切り替わる', btnLabel.includes('停止'), btnLabel.trim());
  await page.screenshot({ path: 'v_alert_geo.png' });

  // --- T6: クールダウン（同一地点は60分間 再通知しない） ---
  await page.click('#alertClose');
  await page.waitForTimeout(200);
  // 位置をわずかに動かして再判定を走らせる（radius は変えない＝notified は保持される）
  await ctx.setGeolocation({ longitude: home.longitude + 0.0002,
                             latitude: home.latitude + 0.0002 });
  await page.waitForTimeout(1500);
  const reshown = await page.evaluate(() => document.getElementById('alert').classList.contains('show'));
  check('T6 同一地点はクールダウン中に再通知されない', reshown === false, `再表示=${reshown}`);

  // --- T7: ポップアップに閾値と判定理由が出る ---
  await page.click('#geoBtn'); // 監視停止
  await page.waitForTimeout(300);
  const mapOK = await page.evaluate(() =>
    !!document.querySelector('.maplibregl-canvas')
    && !!window.mizumichiMap
    && !!window.mizumichiMap.getLayer('spots-dot'));
  check('T7 地図が生成され、危険箇所レイヤが存在する', mapOK);

  const popupText = await page.evaluate(async () => {
    const m = window.mizumichiMap;
    const s = window.mizumichiData.spots[0];
    m.jumpTo({ center: [s.lon, s.lat], zoom: 14 });
    await new Promise(r => setTimeout(r, 700));
    const p = m.project([s.lon, s.lat]);
    m.fire('click', { lngLat: { lng: s.lon, lat: s.lat }, point: p,
                      features: m.queryRenderedFeatures(p, { layers: ['spots-dot'] }),
                      originalEvent: new MouseEvent('click') });
    await new Promise(r => setTimeout(r, 500));
    const el = document.querySelector('.maplibregl-popup-content');
    return el ? el.textContent.replace(/\s+/g, ' ') : '';
  });
  check('T7b ポップアップに閾値と判定理由が表示される',
        /閾値/.test(popupText) && /判定理由/.test(popupText), popupText.slice(0, 80));

  // --- T7c: 地点ラベル（日本語）が描画される ---
  const labels = await page.evaluate(() => {
    document.getElementById('minLv').value = '0';
    document.getElementById('minLv').dispatchEvent(new Event('change'));
    window.mizumichiMap.jumpTo({ zoom: 13.5 });
    const els = [...document.querySelectorAll('.spot-label')];
    return { n: els.length, shown: els.length ? getComputedStyle(els[0]).display : 'none',
             sample: els.length ? els[0].textContent : '' };
  });
  check('T7c 地点名の日本語ラベルが描画される（グリフサーバ不要）',
        labels.n === TOTAL && labels.shown === 'block', JSON.stringify(labels));
  await page.evaluate(() => { const el=document.getElementById('minLv'); el.value='2'; el.dispatchEvent(new Event('change')); });
  await page.waitForTimeout(400);

  // --- T8: ナウキャストトグルはネットワーク失敗時に安全に倒れる ---
  // 実際にネットワークを止めて確かめる。気象庁に繋がる環境でも繋がらない環境でも
  // 同じ結果になるよう、Playwright 側で通信を遮断してから試す。
  await page.route('**/www.jma.go.jp/**', route => route.abort('failed'));
  // check() は「押した後もチェックが入ったまま」を前提に待つので使えない。
  // ここで確かめたいのは逆に、失敗してチェックが外れることそのもの。
  await page.click('#nowcast');
  await page.waitForTimeout(2500);
  const ncChecked = await page.isChecked('#nowcast');
  const ncStat = (await page.textContent('#ncStat')).replace(/\s+/g,' ');
  check('T8 ナウキャスト取得失敗時にチェックが戻り、案内が出る（本体は壊れない）',
        ncChecked === false && /取得できませんでした/.test(ncStat), ncStat.slice(0, 70));
  const stillWorks = (await counts()).reduce((a,b)=>a+b,0) === TOTAL;
  check('T8b ナウキャスト失敗後も本体機能が動作する', stillWorks);
  await page.unroute('**/www.jma.go.jp/**');

  // --- T8c: 取得できたときはレイヤが載る（気象庁への到達性に依らない） ---
  // 基準時刻の応答を偽装するので、外に出られない環境でも成功側の道筋を確かめられる。
  const FAKE_BASETIME = '20260101000000';
  await page.route('**/targetTimes_N1.json', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify([{ basetime: FAKE_BASETIME, validtime: FAKE_BASETIME }])
  }));
  // タイル画像そのものは 1x1 の透明 PNG を返す（地図の見た目は検証対象ではない）
  const PIXEL = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    'base64');
  await page.route('**/hrpns/**', route => route.fulfill({
    status: 200, contentType: 'image/png', body: PIXEL
  }));
  await page.click('#nowcast');
  await page.waitForTimeout(2000);
  const ncOk = await page.evaluate(() => !!window.mizumichiMap.getLayer('nowcast'));
  const ncOkStat = (await page.textContent('#ncStat')).replace(/\s+/g,' ');
  check('T8c ナウキャスト取得成功時にレイヤが載り、基準時刻が出る',
        ncOk && await page.isChecked('#nowcast') && ncOkStat.includes(FAKE_BASETIME),
        ncOkStat.slice(0, 70));
  await page.click('#nowcast');          // 元に戻す
  await page.waitForTimeout(300);
  await page.unroute('**/targetTimes_N1.json');
  await page.unroute('**/hrpns/**');

  // --- T9: JSエラーなし ---
  check('T9 アプリ由来のJSエラーが発生していない', jsErrors.length === 0, jsErrors.slice(0,3).join(' | '));

  // --- 各雨量のスクリーンショット ---
  for (const r of [0, 30, 50, 100]) {
    await page.evaluate(v => { const el=document.getElementById('rain'); el.value=v; el.dispatchEvent(new Event('input')); }, r);
    await page.waitForTimeout(350);
    await page.screenshot({ path: `v_rain_${r}.png` });
  }

  console.log('\n===== 検証結果 =====');
  ok.forEach(s => console.log('  ✅ ' + s));
  ng.forEach(s => console.log('  ❌ ' + s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);
  await browser.close();
  process.exit(ng.length ? 1 : 0);
})();
