"""みずみち — 危険度判定エンジン（サーバ側実装）

docs/04_危険度判定ロジック.md の式を実装したもの。
prototype/risk.js と**同じ式**でなければならない。式を変えたら
  1) docs/04_危険度判定ロジック.md
  2) prototype/risk.js
  3) この risk.py
の3つを必ず同時に直し、test_risk_parity.py で一致を確認すること。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# 種別ごとの既定閾値 [mm/h] — docs/04-3-1
BASE_T60 = {
    "underpass_gravity": 35.0,   # 自然排水のアンダーパス（最も危険）
    "underpass_pump": 55.0,      # ポンプ付きアンダーパス
    "lowland": 50.0,             # 低地・窪地
    "bridge_approach": 60.0,     # 橋詰め
    "river_adjacent": 50.0,      # 河川隣接（本来は水位で判定。暫定で雨量を使う）
}

KIND_LABEL = {
    "underpass_gravity": "アンダーパス（自然排水）",
    "underpass_pump": "アンダーパス（ポンプ有）",
    "lowland": "低地・窪地",
    "bridge_approach": "橋詰め",
    "river_adjacent": "河川隣接",
}

LEVELS = [
    {"lv": 0, "name": "平常", "color": "#9aa4b2", "text": "現時点で冠水の情報はありません"},
    {"lv": 1, "name": "注意", "color": "#e8c33c", "text": "雨が強まれば冠水する可能性のある場所です"},
    {"lv": 2, "name": "警戒", "color": "#ef8c2a", "text": "冠水しやすい場所です。速度を落としてください"},
    {"lv": 3, "name": "危険", "color": "#dc3c3c", "text": "冠水の可能性が高い場所です。迂回を検討してください"},
    {"lv": 4, "name": "極めて危険", "color": "#7a1fa2",
     "text": "冠水により通行できない可能性が高い状況です。進入しないでください",
     "name_reported": "通行不能情報",
     "text_reported": "冠水の報告があります。進入しないでください"},
]


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


@dataclass
class Spot:
    """危険箇所。docs/05-3-1 の spots スキーマに対応する最小サブセット。"""
    id: str
    name: str
    kind: str
    lon: float
    lat: float
    dz: float = 0.0          # 周辺100mとの相対標高 [m]（負なら窪地）
    hist: int = 0            # 過去の冠水確認回数
    t60: Optional[float] = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.t60 is None:
            self.t60 = compute_t60(self)


def compute_t60(spot) -> float:
    """地点の冠水閾値 T60 [mm/h] — docs/04-3"""
    base = BASE_T60.get(getattr(spot, "kind", None) or spot["kind"], 50.0)
    dz = getattr(spot, "dz", None)
    hist = getattr(spot, "hist", None)
    if dz is None:
        dz = spot.get("dz", 0.0)
    if hist is None:
        hist = spot.get("hist", 0)
    terrain = clamp(1 + 0.12 * (dz or 0.0), 0.6, 1.4)
    history = 1 - 0.05 * min(hist or 0, 5)
    return base * terrain * history


def estimate_kikikuru(r60: float) -> int:
    """時間雨量から浸水キキクル相当の階級を推定（what-if 用） — docs/04-7"""
    if r60 >= 100:
        return 4
    if r60 >= 80:
        return 3
    if r60 >= 50:
        return 2
    if r60 >= 30:
        return 1
    return 0


def level_from(s: float) -> int:
    """スコア → レベル — docs/04-4-6"""
    if s >= 80:
        return 4
    if s >= 60:
        return 3
    if s >= 35:
        return 2
    if s >= 15:
        return 1
    return 0


def score(spot, r60: float, r10: float = None, r180: float = None,
          f30: float = 0.0, k: int = None) -> dict:
    """危険度スコアの計算 — docs/04-4

    r60   : 直近60分の降水量 [mm]
    r10   : 直近10分の降水量 [mm]（既定 r60/6）
    r180  : 直近3時間の累積降水量 [mm]（既定 r60*1.5）
    f30   : 今後30分の予測降水量 [mm]
    k     : 浸水キキクル階級 0-4（既定は r60 から推定）
    """
    t60 = getattr(spot, "t60", None) or compute_t60(spot)
    t_burst = t60 * 1.4

    r60 = r60 or 0.0
    r10 = r60 / 6 if r10 is None else r10
    r180 = r60 * 1.5 if r180 is None else r180
    f30 = f30 or 0.0
    k = estimate_kikikuru(r60) if k is None else k

    # 雨量の超過度（時間雨量ベースと短時間強雨ベースの大きい方）
    i_rain = max((r60 + f30) / t60, (r10 * 6) / t_burst)
    # 先行降雨による排水余力の低下
    a = 1 + min(0.30, r180 / 300)
    i_rain *= a

    hist = getattr(spot, "hist", None)
    if hist is None:
        hist = spot.get("hist", 0)

    s = 100 * clamp(
        0.60 * clamp(i_rain, 0, 2) / 2
        + 0.25 * (k / 4)
        + 0.15 * min(hist or 0, 5) / 5,
        0, 1)

    lv = level_from(s)
    reason = f"時間雨量 {r60:.0f}mm（この地点の閾値 {t60:.0f}mm）"
    if r180 > 0:
        reason += f" ／ 先行降雨 {r180:.0f}mm"
    if hist:
        reason += f" ／ 過去の冠水 {hist}回"

    return {"score": s, "level": lv, "t60": t60, "i_rain": i_rain, "k": k, "reason": reason}


def apply_overrides(level: int, user_report: float = 0.0, official_closed: bool = False) -> tuple[int, bool]:
    """実測・確定情報によるオーバーライド — docs/04-4-5

    戻り値: (レベル, 報告由来かどうか)
    """
    if official_closed:
        return 4, True
    if user_report >= 0.8:
        return 4, True
    if user_report >= 0.5:
        return max(level, 3), True
    return level, False


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2点間距離 [m]（Haversine）"""
    r = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))
