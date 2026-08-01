#!/usr/bin/env bash
# Zerodha: BENCH_ITERATIONS × 5 sequential runs (compliant) + summary.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${BENCH_OUT_DIR:-$REPO_ROOT/baselines/zerodha_bench_x5}"
STATS="${BENCH_STATS_PATH:-$REPO_ROOT/baselines/zerodha_bench_x5_stats_latest.txt}"
mkdir -p "$OUT"

export BENCH_ITERATIONS="${BENCH_ITERATIONS:-500}"
export BENCH_WORKERS="${BENCH_WORKERS:-48}"
export BENCH_CACHE="${BENCH_CACHE:-1}"
export BENCH_SKIP_WRITE="${BENCH_SKIP_WRITE:-1}"

PYTHON="${PYTHON:-python3}"

echo "Zerodha benchmark ×5 (pythoncoreengine, sequential): iterations=$BENCH_ITERATIONS workers=$BENCH_WORKERS cache=$BENCH_CACHE"
echo "Output: $OUT"
echo "Stats:  $STATS"
echo

cd "$REPO_ROOT"
for i in $(seq 1 5); do
  echo "=== Run $i / 5 ==="
  "$PYTHON" -m engine.bench_zerodha \
    --report "$OUT/zerodha_run${i}.txt"
  echo "wrote $OUT/zerodha_run${i}.txt"
done

"$PYTHON" "$(dirname "${BASH_SOURCE[0]}")/summarize_runs.py" \
  "$OUT" "$STATS" \
  "${BENCH_X5_MEAN_GATE:-0}" "${BENCH_X5_MEDIAN_GATE:-0}" \
  "# Zerodha x5 latest summary (pythoncoreengine harness)"

echo "Done. Stats: $STATS"
