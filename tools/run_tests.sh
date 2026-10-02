#!/usr/bin/env bash
# ツール一式の検証をまとめて実行する（すべてネットワーク不要）
#   ./tools/run_tests.sh
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
for t in tools/test_risk_parity.py tools/test_build_spots.py tools/test_enrich_dem.py \
         tools/test_fetch_amedas.py tools/test_review_spots.py \
         tools/test_osm_snap.py tools/test_build_region.py tools/test_validate_spots.py tools/test_export_prototype.py tools/test_review_preserved.py; do
  echo
  echo "########## $t ##########"
  python3 "$t" || fail=1
done

echo
echo "########## プロトタイプ（要: ローカルサーバ + playwright） ##########"
echo "  cd prototype && python3 -m http.server 8000 &"
echo "  node ../tools/verify_prototype.js"

exit $fail
