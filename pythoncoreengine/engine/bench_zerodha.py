"""Zerodha-style benchmark harness (phase 8) -- stdlib only.

Runs the 80/15/5 retail/active/HFT workload end to end on the pure-Python
engine: sampledata JSON -> model (LoadJSON + ExpandTrades) -> render
(build_document with the Zerodha theme) -> PDF bytes, in compliant
(PDF/A-4 + PDF/UA-2, default) or plain PDF 2.0 (``--nocomply``) mode, and
with the model cache on (default) or off (``BENCH_CACHE=0`` /
``--uncached``).

Env contract (sampledata/zerodha/README.md):

    BENCH_ITERATIONS  total jobs (default 500; 5000 was the Go-era number
                      -- the Python default is scaled down so a full
                      default run finishes in ~1-2 minutes)
    BENCH_WORKERS     worker count (default 48; clamped to cpu*2 -- jobs
                      run serially because Python layout holds the GIL,
                      but the env semantics are kept)
    BENCH_CACHE       1 = expand trades once, reuse models; 0 = rebuild
                      the model every iteration (bytes identical to 1)
    BENCH_SEED        schedule + expansion seed (default 42)
    BENCH_SKIP_WRITE  1 = skip writing the warm-up PDFs

Usage::

    python3 -m engine.bench_zerodha [--nocomply] [--cached|--uncached]
                                    [--iterations N] [--seed N] [--report PATH]

The full report is written to ``baselines/zerodha_stats_latest.txt``
(project root) and the warm-up PDFs to ``sampledata/zerodha/``.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import ContractNote, TARGET_TRADES, expand_trades, load_json
from .render import build_document

__all__ = [
    "plan_jobs",
    "run_bench",
    "write_warmup_pdfs",
]

_TIERS = ("retail", "active", "hft")
_WEIGHTS = {"retail": 0.80, "active": 0.15, "hft": 0.05}
_FIXTURES = {
    "retail": "retail_investor.json",
    "active": "active_trader.json",
    "hft": "hft_algo.json",
}

_DEFAULT_ITERATIONS = 500
_DEFAULT_SEED = 42
_DEFAULT_WORKERS = 48

_PAGE_COUNT_RE = re.compile(rb"/Count (\d+)")


def _repo_root() -> Path:
    """The project root (pythoncoreengine/), mirroring engine/bench.py."""
    return Path(__file__).resolve().parent.parent


_DEFAULT_DATA_DIR = _repo_root() / "sampledata" / "zerodha"
_DEFAULT_REPORT = _repo_root() / "baselines" / "zerodha_stats_latest.txt"


def plan_jobs(iterations: int, seed: int = _DEFAULT_SEED) -> List[str]:
    """The deterministic 80/15/5 tier schedule for ``iterations`` jobs."""
    rng = random.Random(seed)
    jobs: List[str] = []
    for _ in range(iterations):
        draw = rng.random()
        if draw < _WEIGHTS["retail"]:
            jobs.append("retail")
        elif draw < _WEIGHTS["retail"] + _WEIGHTS["active"]:
            jobs.append("active")
        else:
            jobs.append("hft")
    return jobs


def _load_base(data_dir: Path) -> Dict[str, ContractNote]:
    """The raw (unexpanded) note per tier, read once from the fixtures."""
    return {tier: load_json(data_dir / _FIXTURES[tier]) for tier in _TIERS}


def _expanded_models(
    base: Dict[str, ContractNote], seed: int
) -> Dict[str, ContractNote]:
    """Cached models: retail unexpanded, active -> 40, hft -> 2000 trades."""
    return {
        tier: (
            base[tier]
            if TARGET_TRADES[tier] <= len(base[tier].trades)
            else expand_trades(base[tier], TARGET_TRADES[tier], seed)
        )
        for tier in _TIERS
    }


def run_bench(
    iterations: int,
    *,
    seed: int = _DEFAULT_SEED,
    cached: bool = True,
    compliant: bool = True,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """One timed run; returns the measurement dict (see format_report).

    Every job renders a full PDF.  In cached mode the expanded models are
    built once up front; in uncached mode each job re-expands with the
    same seed, so both modes produce byte-identical documents and the
    cache dimension only measures model-rebuild cost.
    """
    data_dir = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    base = _load_base(data_dir)
    models = _expanded_models(base, seed) if cached else base
    jobs = plan_jobs(iterations, seed)

    per_tier: Dict[str, Dict[str, float]] = {
        tier: {"jobs": 0.0, "time": 0.0} for tier in _TIERS
    }
    outputs: Dict[str, bytes] = {}
    started = time.perf_counter()
    for tier in jobs:
        if cached:
            note = models[tier]
        else:
            note = (
                base[tier]
                if TARGET_TRADES[tier] <= len(base[tier].trades)
                else expand_trades(base[tier], TARGET_TRADES[tier], seed)
            )
        begin = time.perf_counter()
        data = build_document(note, compliant=compliant)
        per_tier[tier]["time"] += time.perf_counter() - begin
        per_tier[tier]["jobs"] += 1.0
        outputs[tier] = data
    total_time = time.perf_counter() - started

    tiers: Dict[str, Dict[str, Any]] = {}
    for tier in _TIERS:
        count = int(per_tier[tier]["jobs"])
        seconds = per_tier[tier]["time"]
        tiers[tier] = {
            "jobs": count,
            "time": seconds,
            "ms_per_job": 1000.0 * seconds / count if count else 0.0,
        }
    return {
        "iterations": iterations,
        "seed": seed,
        "cache": cached,
        "compliant": compliant,
        "total_time": total_time,
        "jobs_per_sec": iterations / total_time if total_time else 0.0,
        "tiers": tiers,
        "output_bytes": {tier: len(data) for tier, data in outputs.items()},
        "pages": {tier: _page_count(data) for tier, data in outputs.items()},
        "md5": {tier: hashlib.md5(data).hexdigest() for tier, data in outputs.items()},
    }


def _page_count(data: bytes) -> Optional[int]:
    """The pages-tree /Count (the only /Count in the emitted bytes)."""
    match = _PAGE_COUNT_RE.search(data)
    return int(match.group(1)) if match is not None else None


def write_warmup_pdfs(
    models: Dict[str, ContractNote],
    out_dir: Path,
    *,
    compliant: bool = True,
) -> Dict[str, Path]:
    """Write one warm-up PDF per tier (``zerodha_<tier>[_nocomply]_output.pdf``)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "output" if compliant else "nocomply_output"
    paths: Dict[str, Path] = {}
    for tier in _TIERS:
        path = out_dir / ("zerodha_%s_%s.pdf" % (tier, suffix))
        path.write_bytes(build_document(models[tier], compliant=compliant))
        paths[tier] = path
    return paths


def _fmt_bytes(value: int) -> str:
    if value >= 1 << 20:
        return "%.2f MiB" % (value / (1 << 20))
    if value >= 1 << 10:
        return "%.1f KiB" % (value / (1 << 10))
    return "%d B" % value


def format_report(results: Dict[str, Any], *, workers: int) -> List[str]:
    """Human-readable report lines for one run (phase-6 bench style)."""
    mode = "pdfa4+pdfua2" if results["compliant"] else "pdf20"
    cache = "on" if results["cache"] else "off"
    lines = [
        "zerodha workload: %d jobs (80/15/5 retail/active/hft) %s cache=%s"
        % (results["iterations"], mode, cache),
        "  total time:        %.3f s" % results["total_time"],
        "  jobs/sec:          %.2f (workers=%d, serial)" % (
            results["jobs_per_sec"], workers,
        ),
    ]
    for tier in _TIERS:
        stats = results["tiers"][tier]
        if not stats["jobs"]:
            continue
        pages = results["pages"].get(tier)
        size = results["output_bytes"].get(tier, 0)
        lines.append(
            "  %-6s %5d jobs %7.2f s (%6.1f ms/job, %4s pages, %10s)"
            % (
                tier,
                stats["jobs"],
                stats["time"],
                stats["ms_per_job"],
                "?" if pages is None else str(pages),
                _fmt_bytes(size),
            )
        )
    total_bytes = sum(results["output_bytes"].values())
    lines.append("  output bytes:      total %d (%s)" % (total_bytes, _fmt_bytes(total_bytes)))
    lines.append("  compliance:        %s (embedded fonts)" % mode)
    lines.append("  cache:             %s (seed %d)" % (cache, results["seed"]))
    retail_md5 = results["md5"].get("retail")
    if retail_md5 is not None:
        lines.append("  deterministic:     yes (retail md5 %s)" % retail_md5)
    return lines


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from None


def _clamp_workers(requested: int) -> int:
    """Clamp the BENCH_WORKERS value to at most ``cpu_count() * 2``."""
    return max(1, min(requested, (os.cpu_count() or 1) * 2))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nocomply", action="store_true", help="plain PDF 2.0 (no A-4/UA-2)"
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--cached", dest="cached", action="store_true",
        help="reuse expanded models (default, BENCH_CACHE=1)",
    )
    cache_group.add_argument(
        "--uncached", dest="cached", action="store_false",
        help="rebuild the model every iteration (BENCH_CACHE=0)",
    )
    parser.set_defaults(cached=None)
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="total jobs (default BENCH_ITERATIONS or %d)" % _DEFAULT_ITERATIONS,
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="schedule + expansion seed (default BENCH_SEED or %d)" % _DEFAULT_SEED,
    )
    parser.add_argument(
        "--report", type=str, default=None,
        help="report file (default baselines/zerodha_stats_latest.txt)",
    )
    args = parser.parse_args(argv)

    iterations = (
        args.iterations
        if args.iterations is not None
        else _env_int("BENCH_ITERATIONS", _DEFAULT_ITERATIONS)
    )
    seed = args.seed if args.seed is not None else _env_int("BENCH_SEED", _DEFAULT_SEED)
    cached = (
        args.cached if args.cached is not None
        else os.environ.get("BENCH_CACHE", "1") != "0"
    )
    compliant = not args.nocomply
    workers = _clamp_workers(_env_int("BENCH_WORKERS", _DEFAULT_WORKERS))
    skip_write = os.environ.get("BENCH_SKIP_WRITE") == "1"
    report = Path(args.report) if args.report is not None else _DEFAULT_REPORT

    results = run_bench(
        iterations, seed=seed, cached=cached, compliant=compliant
    )
    lines = format_report(results, workers=workers)
    header = "run %s  iterations=%d seed=%d cache=%d compliant=%s workers=%d" % (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        iterations,
        seed,
        int(cached),
        compliant,
        workers,
    )
    for line in lines:
        print(line)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote %s" % report)

    if skip_write:
        print("BENCH_SKIP_WRITE=1: warm-up PDFs skipped")
        return 0
    models = _expanded_models(_load_base(_DEFAULT_DATA_DIR), seed)
    paths = write_warmup_pdfs(models, _DEFAULT_DATA_DIR, compliant=compliant)
    for tier, path in paths.items():
        print("wrote %s (%d bytes)" % (path, path.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
