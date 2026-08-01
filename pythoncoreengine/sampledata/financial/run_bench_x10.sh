#!/usr/bin/env bash
# Financial report gold standard: BENCH_ITERATIONS × 10 sequential runs (compliant).
# Produces baselines/financial_bench_x10/ + financial_bench_x10_stats_latest.txt
# in the same summary format as zerodha_bench_x10_stats_latest.txt.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${BENCH_OUT_DIR:-$REPO_ROOT/baselines/financial_bench_x10}"
STATS="${BENCH_STATS_PATH:-$REPO_ROOT/baselines/financial_bench_x10_stats_latest.txt}"
mkdir -p "$OUT"

export BENCH_ITERATIONS="${BENCH_ITERATIONS:-100}"
export BENCH_WORKERS="${BENCH_WORKERS:-48}"
export BENCH_CACHE="${BENCH_CACHE:-1}"
export BENCH_SKIP_WRITE="${BENCH_SKIP_WRITE:-1}"

PYTHON="${PYTHON:-python3}"
EXTRA_ARGS=()
if [[ "${BENCH_NOCOMPLY:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--nocomply)
fi

echo "Financial benchmark ×10 (compliant, sequential): iterations=$BENCH_ITERATIONS workers=$BENCH_WORKERS cache=$BENCH_CACHE"
echo "Output: $OUT"
echo "Stats:  $STATS"
echo

cd "$REPO_ROOT"
for i in $(seq 1 10); do
  echo "=== Run $i / 10 ==="
  "$PYTHON" -m engine.bench_financial \
    "${EXTRA_ARGS[@]}" \
    --report "$OUT/financial_run${i}.txt"
  echo "wrote $OUT/financial_run${i}.txt"
done

"$PYTHON" "$(dirname "${BASH_SOURCE[0]}")/summarize_runs.py" \
  "$OUT" "$STATS" \
  "${BENCH_X10_MEAN_GATE:-0}" "${BENCH_X10_MEDIAN_GATE:-0}" \
  "# Financial x10 latest summary (pythoncoreengine harness)"

echo "Done. Stats: $STATS"
