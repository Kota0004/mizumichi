/**
 * みずみち — 危険度判定エンジン（プロトタイプ実装）
 *
 * docs/04_危険度判定ロジック.md の式をそのまま実装したもの。
 * サーバ側（Python）と同じ式を使うこと。式を変えるときは必ずドキュメントも直す。
 */
(function (global) {
  'use strict';

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  /** 種別ごとの既定閾値 [mm/h] — docs/04-3-1 */
  const BASE_T60 = {
    underpass_gravity: 35,
    underpass_pump: 55,
    lowland: 50,
    bridge_approach: 60,
    river_adjacent: 50 // 本来は河川水位で判定する。プロトタイプでは雨量で近似
  };

  const KIND_LABEL = {
    underpass_gravity: 'アンダーパス（自然排水）',
    underpass_pump: 'アンダーパス（ポンプ有）',
    lowland: '低地・窪地',
    bridge_approach: '橋詰め',
    river_adjacent: '河川隣接'
  };

  const LEVELS = [
    { lv: 0, name: '平常',       color: '#9aa4b2', text: '現時点で冠水の情報はありません' },
    { lv: 1, name: '注意',       color: '#e8c33c', text: '雨が強まれば冠水する可能性のある場所です' },
    { lv: 2, name: '警戒',       color: '#ef8c2a', text: '冠水しやすい場所です。速度を落としてください' },
    { lv: 3, name: '危険',       color: '#dc3c3c', text: '冠水の可能性が高い場所です。迂回を検討してください' },
    { lv: 4, name: '極めて危険', color: '#7a1fa2', text: '冠水により通行できない可能性が高い状況です。進入しないでください',
      // 実際の報告・公的な通行止めでレベル4になった場合はこちらを使う（docs/04-4-5）
      nameReported: '通行不能情報', textReported: '冠水の報告があります。進入しないでください' }
  ];

  /** 地点の冠水閾値 T60 [mm/h] — docs/04-3 */
  function computeT60(spot) {
    const base = BASE_T60[spot.kind] != null ? BASE_T60[spot.kind] : 50;
    const terrain = clamp(1 + 0.12 * (spot.dz || 0), 0.6, 1.4); // 相対標高補正
    const history = 1 - 0.05 * Math.min(spot.hist || 0, 5);     // 履歴補正
    return base * terrain * history;
  }

  /** 時間雨量から浸水キキクル相当の階級を推定（what-if モード用） — docs/04-7 */
  function estimateKikikuru(r60) {
    if (r60 >= 100) return 4;
    if (r60 >= 80) return 3;
    if (r60 >= 50) return 2;
    if (r60 >= 30) return 1;
    return 0;
  }

  /**
   * 危険度スコアの計算 — docs/04-4
   * @param {object} spot  地点（t60 が未計算なら内部で算出）
   * @param {object} obs   { r60, r10, r180, f30, k }  未指定の項目は r60 から推定
   * @returns {{score:number, level:number, t60:number, iRain:number, k:number, reason:string}}
   */
  function score(spot, obs) {
    const t60 = spot.t60 != null ? spot.t60 : computeT60(spot);
    const tBurst = t60 * 1.4;

    const r60 = obs.r60 || 0;
    const r10 = obs.r10 != null ? obs.r10 : r60 / 6;      // 10分値 [mm]
    const r180 = obs.r180 != null ? obs.r180 : r60 * 1.5; // 3時間累積 [mm]
    const f30 = obs.f30 || 0;                             // 30分先予測 [mm]
    const k = obs.k != null ? obs.k : estimateKikikuru(r60);

    // 雨量の超過度（時間雨量ベースと短時間強雨ベースの大きい方）
    let iRain = Math.max((r60 + f30) / t60, (r10 * 6) / tBurst);
    // 先行降雨による排水余力の低下
    const a = 1 + Math.min(0.30, r180 / 300);
    iRain *= a;

    const s = 100 * clamp(
      0.60 * clamp(iRain, 0, 2) / 2 +
      0.25 * (k / 4) +
      0.15 * Math.min(spot.hist || 0, 5) / 5,
      0, 1
    );

    const level = levelFrom(s);
    const reason = `時間雨量 ${r60.toFixed(0)}mm（この地点の閾値 ${t60.toFixed(0)}mm）` +
      (r180 > 0 ? ` ／ 先行降雨 ${r180.toFixed(0)}mm` : '') +
      (spot.hist ? ` ／ 過去の冠水 ${spot.hist}回` : '');

    return { score: s, level, t60, iRain, k, reason };
  }

  /** スコア → レベル — docs/04-4-6 */
  function levelFrom(s) {
    if (s >= 80) return 4;
    if (s >= 60) return 3;
    if (s >= 35) return 2;
    if (s >= 15) return 1;
    return 0;
  }

  /** 2点間距離 [m]（Haversine） */
  function distanceM(lon1, lat1, lon2, lat2) {
    const R = 6371000;
    const toRad = (d) => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  global.MizuMichi = {
    clamp, computeT60, estimateKikikuru, score, levelFrom, distanceM,
    LEVELS, KIND_LABEL, BASE_T60
  };
})(window);
