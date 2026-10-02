/* 検証スクリプトの共通処理 */

/* CDN（unpkg）に出られない環境向けの逃げ道。
 *
 *   MAPLIBRE_DIR=$PWD/node_modules/maplibre-gl/dist node verify_prototype.js
 *
 * のように node_modules/maplibre-gl/dist を渡すと、MapLibre をそこから読ませる。
 * 未設定なら何もしないので、CDN に出られる環境（CI 含む）の挙動は変わらない。
 * アプリ側は書き換えないので、検証しているのは本番と同じコード。
 */
async function useLocalMaplibre(page) {
  const dir = process.env.MAPLIBRE_DIR;
  if (!dir) return false;
  const fs = require('fs'), path = require('path');
  await page.route('**/unpkg.com/maplibre-gl**', route => {
    const css = route.request().url().endsWith('.css');
    const file = css ? 'maplibre-gl.css' : 'maplibre-gl.js';
    route.fulfill({
      status: 200,
      contentType: css ? 'text/css' : 'application/javascript',
      body: fs.readFileSync(path.join(dir, file))
    });
  });
  return true;
}

/* 検証用の現在地を、読み込まれたデータから決める。
 *
 * 座標を決め打ちにすると、データを取り込み直して地点が少し動いただけで
 * 「アラートが出ない」と落ちる。実際、千葉の未レビュー分を再取得したら
 * 最寄りが 460m から 519m になり、半径500mから外れて落ちた。
 * 製品の不具合ではなくテストの脆さなので、地点の側から現在地を決める。
 *
 * 地点として使える（precision=point）ものを選ぶ。推定でしかない地点は
 * 設計上アラートを鳴らさないため、対象にすると必ず落ちる。
 */
async function positionNearAlertableSpot(page, metersAway = 200) {
  const spot = await page.evaluate(() => {
    const d = window.mizumichiData;
    if (!d || !d.spots) return null;
    const s = d.spots.find(x => (x.precision || 'point') === 'point'
                             && x.lon != null && x.lat != null);
    return s ? { lon: s.lon, lat: s.lat, name: s.name, id: s.id } : null;
  });
  if (!spot) throw new Error('アラート対象にできる地点がデータにありません');
  // 真南にずらす（緯度1度 ≒ 111km）
  const dLat = metersAway / 111000;
  return { longitude: spot.lon, latitude: spot.lat - dLat, spot };
}

module.exports = { useLocalMaplibre, positionNearAlertableSpot };
