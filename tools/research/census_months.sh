#!/usr/bin/env bash
# Run the calibration census for several months, one after another, resumably (PRD v3 D1).
# Per month: sample (skipped when the manifest exists), fetch with retries until no unit fails,
# build twice and compare hashes, verify. Progress lines go to data/census-driver.log; each
# month's full log to data/census-<month>.log.
#   nohup tools/research/census_months.sh 2025-08 2025-09 > /dev/null 2>&1 &
set -u
cd "$(dirname "$0")/../.."
unset VIRTUAL_ENV
DRIVER=data/census-driver.log
for MONTH in "$@"; do
  SEED="${MONTH//-/}01"
  LOG="data/census-$MONTH.log"
  echo "=== $MONTH start $(date -u +%FT%TZ)" | tee -a "$DRIVER"
  if [ ! -f "data/raw/_census/census-$MONTH.json" ]; then
    if ! uv run syntha-ingest --rps 20 census-sample --month "$MONTH" --seed "$SEED" --concurrency 16 >> "$LOG" 2>&1; then
      echo "=== $MONTH SAMPLE FAILED (see $LOG)" | tee -a "$DRIVER"; continue
    fi
  fi
  ok=0
  for attempt in 1 2 3 4 5; do
    if uv run syntha-ingest --rps 20 census-fetch --month "$MONTH" --concurrency 16 --prices-rps 40 >> "$LOG" 2>&1; then ok=1; break; fi
    echo "=== $MONTH fetch attempt $attempt left failures; retrying in 90s" | tee -a "$DRIVER"; sleep 90
  done
  if [ "$ok" != 1 ]; then echo "=== $MONTH FETCH FAILED after 5 attempts" | tee -a "$DRIVER"; continue; fi
  uv run syntha-ingest census-build --month "$MONTH" > "data/census-$MONTH-build1.json" 2>> "$LOG" || { echo "=== $MONTH BUILD FAILED" | tee -a "$DRIVER"; continue; }
  uv run syntha-ingest census-build --month "$MONTH" > "data/census-$MONTH-build2.json" 2>> "$LOG"
  uv run syntha-ingest census-verify --month "$MONTH" >> "$LOG" 2>&1 && verified=verified || verified=VERIFY-FAILED
  summary=$(python3 - "$MONTH" <<'PY'
import json, sys
m = sys.argv[1]
a = json.load(open(f"data/census-{m}-build1.json")); b = json.load(open(f"data/census-{m}-build2.json"))
same = "bit-identical" if a["content_hash"] == b["content_hash"] else "HASH-MISMATCH"
q = a["quality"]
print(f"{a['n_markets']} markets {a['n_observations']} obs fill10={q['observations_with_fill_10m']} mid={q['observations_with_mid']} hash={a['content_hash'][:12]} {same}")
PY
)
  echo "=== $MONTH done $(date -u +%FT%TZ): $summary $verified" | tee -a "$DRIVER"
done
echo "=== driver finished $(date -u +%FT%TZ)" | tee -a "$DRIVER"
