#!/usr/bin/env python3
"""tools/risk.py と prototype/risk.js が同じ結果を返すことを検証する。

クライアント（アプリ）とサーバ（取込パイプライン）で危険度がずれると、
「アプリの表示」と「通知の判定」が食い違って信用を失う。式を変えたら必ず実行すること。

    python3 tools/test_risk_parity.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RISK_JS = ROOT / "prototype" / "risk.js"

KINDS = ["underpass_gravity", "underpass_pump", "lowland", "bridge_approach", "river_adjacent"]
DZS = [-3.0, -1.8, -0.5, 0.0, 1.5, 4.0]     # clamp の両端をまたぐ値を含める
HISTS = [0, 1, 3, 5, 7]                      # min(hist,5) の頭打ちも確認
RAINS = [0, 5, 20, 23.3, 30, 50, 80, 100, 150]
F30S = [0, 12]


def cases():
    for kind in KINDS:
        for dz in DZS:
            for hist in HISTS:
                for r60 in RAINS:
                    for f30 in F30S:
                        yield {"kind": kind, "dz": dz, "hist": hist, "r60": r60, "f30": f30}


JS_RUNNER = """
global.window = {};
require(process.argv[2]);
const M = global.window.MizuMichi;
const cases = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const out = cases.map(c => {
  const spot = { kind: c.kind, dz: c.dz, hist: c.hist };
  spot.t60 = M.computeT60(spot);
  const r = M.score(spot, { r60: c.r60, f30: c.f30 });
  return { t60: r.t60, score: r.score, level: r.level, k: r.k };
});
process.stdout.write(JSON.stringify(out));
"""


def run_js(cs):
    """ケース数が多く argv に載らないため、入力は一時ファイル経由で node に渡す。"""
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = Path(tmp) / "cases.json"
        cases_path.write_text(json.dumps(list(cs)), encoding="utf-8")
        runner = Path(tmp) / "runner.js"
        runner.write_text(JS_RUNNER, encoding="utf-8")
        res = subprocess.run(["node", str(runner), str(RISK_JS), str(cases_path)],
                             capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"node の実行に失敗しました:\n{res.stderr}")
    return json.loads(res.stdout)


def main():
    cs = list(cases())
    js = run_js(cs)
    bad = []
    for c, j in zip(cs, js):
        spot = risk.Spot(id="t", name="t", kind=c["kind"], lon=0, lat=0,
                         dz=c["dz"], hist=c["hist"])
        p = risk.score(spot, r60=c["r60"], f30=c["f30"])
        if (abs(p["t60"] - j["t60"]) > 1e-9
                or abs(p["score"] - j["score"]) > 1e-9
                or p["level"] != j["level"]
                or p["k"] != j["k"]):
            bad.append((c, p, j))

    print(f"検証ケース数: {len(cs)}")
    if bad:
        print(f"❌ 不一致 {len(bad)} 件（先頭3件）")
        for c, p, j in bad[:3]:
            print(f"  入力 {c}")
            print(f"    py: t60={p['t60']:.6f} S={p['score']:.6f} lv={p['level']} k={p['k']}")
            print(f"    js: t60={j['t60']:.6f} S={j['score']:.6f} lv={j['level']} k={j['k']}")
        return 1
    print("✅ risk.py と prototype/risk.js は全ケースで一致")

    # docs/04-7 の計算例との突き合わせ
    spot = risk.Spot(id="doc", name="計算例", kind="underpass_gravity", lon=0, lat=0, dz=-1.8, hist=3)
    expected = {20: (37.3, 2), 30: (59.7, 2), 50: (81.5, 4), 80: (87.8, 4)}
    print("\ndocs/04-7 の計算例との照合（T60 = "
          f"{spot.t60:.1f} mm/h、ドキュメント記載値 23.3）")
    ng = abs(spot.t60 - 23.3) > 0.05
    for r60, (exp_s, exp_lv) in expected.items():
        got = risk.score(spot, r60=r60)
        mark = "✅" if abs(got["score"] - exp_s) < 0.15 and got["level"] == exp_lv else "❌"
        if mark == "❌":
            ng = True
        print(f"  {mark} {r60:>3} mm/h → S={got['score']:.1f} (doc {exp_s}) / Lv{got['level']} (doc {exp_lv})")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
