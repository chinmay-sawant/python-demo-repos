#!/usr/bin/env python3
"""Aggregate multi-run financial bench reports into a Zerodha-style summary.

Parses per-run logs that contain (from engine.bench_financial)::

    Throughput:        N ops/sec
    Avg Latency:       N ms
    Max Memory Allocated: N MB

Writes a markdown table summary matching baselines/zerodha_bench_x10_stats_latest.txt.

Usage::

    python3 summarize_runs.py OUT_DIR STATS_PATH [MEAN_GATE] [MEDIAN_GATE] [TITLE]
"""

from __future__ import annotations

import re
import statistics as stats
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(
            "usage: summarize_runs.py OUT_DIR STATS_PATH "
            "[MEAN_GATE] [MEDIAN_GATE] [TITLE]",
            file=sys.stderr,
        )
        return 2

    out = Path(argv[0])
    stats_path = Path(argv[1])
    mean_gate = float(argv[2]) if len(argv) > 2 else 0.0
    median_gate = float(argv[3]) if len(argv) > 3 else 0.0
    title = (
        argv[4]
        if len(argv) > 4
        else "# Financial xN latest summary (pythoncoreengine harness)"
    )

    # Prefer financial_run*.txt / financial_py_run*.txt / financial_nocomply_run*.txt
    paths = sorted(
        list(out.glob("financial*run*.txt")) + list(out.glob("financial_run*.txt")),
        key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)) if re.search(r"(\d+)", p.stem) else 0,
    )
    # Deduplicate while preserving order
    seen = set()
    unique_paths = []
    for path in paths:
        if path.name in seen:
            continue
        seen.add(path.name)
        unique_paths.append(path)

    runs = []
    for path in unique_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        throughput = re.search(r"Throughput:\s+([0-9.]+)\s*ops/sec", text)
        latency = re.search(r"Avg Latency:\s+([0-9.]+)\s*ms", text)
        allocated = re.search(r"Max Memory Allocated:\s+([0-9.]+)\s*MB", text)
        # Fallback to jobs/sec / ms/job if the Go-style labels are missing.
        if throughput is None:
            throughput = re.search(r"jobs/sec:\s+([0-9.]+)", text)
        if latency is None:
            latency = re.search(r"ms/job:\s+([0-9.]+)", text)
        if throughput:
            runs.append(
                (
                    path.name,
                    float(throughput.group(1)),
                    float(latency.group(1)) if latency else 0.0,
                    float(allocated.group(1)) if allocated else 0.0,
                )
            )

    lines = [title, "", f"Runs: {len(runs)}"]
    if runs:
        throughputs = [r[1] for r in runs]
        latencies = [r[2] for r in runs]
        allocations = [r[3] for r in runs]
        mean = stats.mean(throughputs)
        median = stats.median(throughputs)
        lines.extend(
            [
                f"Best throughput: {max(throughputs):.2f} ops/sec",
                f"Worst throughput: {min(throughputs):.2f} ops/sec",
                f"Mean throughput: {mean:.2f} ops/sec",
                f"Median throughput: {median:.2f} ops/sec",
                f"Stddev throughput: {(stats.stdev(throughputs) if len(throughputs) > 1 else 0.0):.2f} ops/sec",
                f"Mean avg latency: {stats.mean(latencies):.3f} ms",
                f"Mean peak allocated: {stats.mean(allocations):.2f} MB",
                "",
                "| Run | Throughput | Avg latency | Peak allocated |",
                "|-----|-----------:|------------:|---------------:|",
            ]
        )
        lines.extend(
            f"| {name} | {throughput:.2f} ops/sec | {latency:.3f} ms | {allocated:.2f} MB |"
            for name, throughput, latency, allocated in runs
        )

    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSummary: {stats_path}")

    if runs and mean_gate > 0 and mean < mean_gate:
        raise SystemExit(f"mean throughput gate failed: {mean:.2f} < {mean_gate:.2f}")
    if runs and median_gate > 0 and median < median_gate:
        raise SystemExit(
            f"median throughput gate failed: {median:.2f} < {median_gate:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
