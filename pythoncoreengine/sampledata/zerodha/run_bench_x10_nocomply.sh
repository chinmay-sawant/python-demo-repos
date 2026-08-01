#!/usr/bin/env bash
# Zerodha non-compliant: BENCH_ITERATIONS × 10 sequential runs (PDF 2.0 only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export BENCH_OUT_DIR="${BENCH_OUT_DIR:-$REPO_ROOT/baselines/zerodha_bench_x10_nocomply}"
export BENCH_STATS_PATH="${BENCH_STATS_PATH:-$REPO_ROOT/baselines/zerodha_bench_x10_nocomply_stats_latest.txt}"
export BENCH_NOCOMPLY=1

# Reuse the compliant x10 driver with nocomply flag.
exec "$(dirname "${BASH_SOURCE[0]}")/run_bench_x10.sh"
