/* 「いまの雨量」モードの検証（Playwright）
 *
 * 事前に取り込み済みの雨量が要る。通信せずに用意するには:
 *   cd mizumichi（リポジトリの一番上）
 *   python3 tools/fetch_amedas.py --spots data/spots_chiba.json \
 *       --fixture-dir tools/fixtures/amedas --out prototype/data/risk_latest.json
 *   cd prototype && python3 -m http.server 8000 &
 *   node ../tools/verify_live.js
 *
 * ※ フィクスチャは架空の観測値。確認が済んだらファイルを消すこと。
 */
const { chromium } = require('playwright');
const { useLocalMaplibre } = require('./verify_support');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const ok=[],ng=[]; const check=(n,c,e='')=>(c?ok:ng).push(n+(e?` — ${e}`:''));
(async()=>{
  const b=await chromium.launch({executablePath: process.env.CHROMIUM_PATH || undefined});
  const p=await b.newPage({viewport:{width:1280,height:960}});
  await useLocalMaplibre(p);
  const errs=[]; p.on('pageerror',e=>errs.push(e.message));
  p.on('console',m=>{const t=m.text();
    if(m.type()==='error'&&!/ERR_TUNNEL|Failed to fetch|Failed to load resource/.test(t))errs.push(t);});
  await p.goto(`${BASE}/index.html`,{waitUntil:'load'});
  await p.waitForSelector('.cnt'); await p.waitForTimeout(900);

  // 雨量が未取込だとボタンは無効のまま。原因の分からないタイムアウトで
  // 落ちるより、何が足りないかを言って終わる方がよい。
  if (await p.isDisabled('#liveBtn')) {
    console.log('検証できません: prototype/data/risk_latest.json がありません。');
    console.log('冒頭のコメントにある fetch_amedas.py の手順で用意してください。');
    await b.close();
    process.exit(2);
  }

  check('いまの雨量ボタンが有効になる', !(await p.isDisabled('#liveBtn')));
  const stat0 = await p.textContent('#liveStat');
  check('観測時刻と経過が出る', /観測時刻/.test(stat0) && /分前/.test(stat0), stat0.replace(/\s+/g,' ').slice(0,70));

  const before = await p.$$eval('.cnt', e=>e.map(x=>parseInt(x.textContent)));
  await p.click('#liveBtn');
  await p.waitForTimeout(700);
  const after = await p.$$eval('.cnt', e=>e.map(x=>parseInt(x.textContent)));
  check('いまの雨量に切り替えると判定が変わる', JSON.stringify(before)!==JSON.stringify(after),
        `${before.join('/')} → ${after.join('/')}`);
  check('切替中はスライダーが無効になる', await p.isDisabled('#rain'));
  check('ボタンの表示が使用中に変わる', /使用中/.test(await p.textContent('#liveBtn')));

  // 判定理由がサーバ側の文言になっている
  const reason = await p.evaluate(()=>{
    const m=window.mizumichiMap; const f=m.getSource('spots')._data.features[0];
    return f ? f.properties.reason : ''; });
  check('取り込み済みの判定理由が使われる', /時間雨量/.test(reason), reason.slice(0,60));

  await p.click('#liveBtn'); await p.waitForTimeout(600);
  check('解除するとスライダーが戻る', !(await p.isDisabled('#rain')));

  check('JSエラーなし', errs.length===0, errs.slice(0,2).join(' | '));
  if (process.env.SHOTS) await p.screenshot({path:'live_mode.png'});
  console.log('\n===== いまの雨量モード =====');
  ok.forEach(s=>console.log('  OK '+s)); ng.forEach(s=>console.log('  NG '+s));
  console.log(`\n合計: ${ok.length} 件成功 / ${ng.length} 件失敗`);
  await b.close(); process.exit(ng.length?1:0);
})();
