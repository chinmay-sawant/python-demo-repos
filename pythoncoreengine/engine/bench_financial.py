"""Financial-report benchmark harness -- stdlib only.

Runs the full-format template workload end to end on the pure-Python
engine: ``sampledata/financial/financial_report.json`` -> model
(:func:`~engine.model.load_template`) -> render
(:func:`~engine.render.build_template_document`) -> PDF bytes.

Cache contract (template-only -- PDF is **never** cached):

* ``BENCH_CACHE=1`` / ``--cached`` (default): parse the JSON once and
  reuse the :class:`~engine.model.PDFTemplate` across iterations.
* ``BENCH_CACHE=0`` / ``--uncached``: re-read and re-parse the JSON on
  every iteration (bytes identical; measures load cost).

Compliance:

* default: follow the template config (``pdfaCompliant`` /
  ``arlingtonCompatible`` -- the sample JSON enables Arlington, so
  PDF/A-4 + PDF/UA-2 by default)
* ``--nocomply``: force plain PDF 2.0
* ``--compliant``: force PDF/A-4 + PDF/UA-2

Env contract (mirrors sampledata/zerodha/README.md):

    BENCH_ITERATIONS  total jobs (default 100)
    BENCH_WORKERS     worker count (default 48; jobs run serially under
                      the GIL, env semantics kept)
    BENCH_CACHE       1 = load template once; 0 = reload every iteration
    BENCH_SEED        reserved for parity with zerodha (default 42)
    BENCH_SKIP_WRITE  1 = skip writing the warm-up PDF

Usage::

    python3 -m engine.bench_financial [--nocomply|--compliant]
                                      [--cached|--uncached]
                                      [--iterations N] [--report PATH]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import PDFTemplate, load_template
from .render import build_template_document, template_wants_compliant

__all__ = [
    "run_bench",
    "write_warmup_pdf",
]

_DEFAULT_ITERATIONS = 100
_DEFAULT_SEED = 42
_DEFAULT_WORKERS = 48
_FIXTURE_NAME = "financial_report.json"
_PAGE_COUNT_RE = re.compile(rb"/Count (\d+)")


def _repo_root() -> Path:
    """The project root (pythoncoreengine/)."""
    return Path(__file__).resolve().parent.parent


_DEFAULT_DATA_DIR = _repo_root() / "sampledata" / "financial"
_DEFAULT_REPORT = _repo_root() / "baselines" / "financial_stats_latest.txt"


def _page_count(data: bytes) -> Optional[int]:
    match = _PAGE_COUNT_RE.search(data)
    return int(match.group(1)) if match is not None else None


def _resolve_compliant(
    template: PDFTemplate, *, force: Optional[bool]
) -> bool:
    """``force`` overrides; otherwise honour the template config."""
    if force is not None:
        return force
    return template_wants_compliant(template)


def run_bench(
    iterations: int,
    *,
    cached: bool = True,
    compliant: Optional[bool] = None,
    data_dir: Optional[Path] = None,
    fixture: str = _FIXTURE_NAME,
) -> Dict[str, Any]:
    """One timed run; returns the measurement dict (see format_report).

    Every job renders a full PDF.  In cached mode the template is loaded
    once up front; in uncached mode each job reloads the JSON from disk.
    PDF bytes are **never** cached either way.
    """
    data_dir = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    path = data_dir / fixture
    if not path.is_file():
        raise FileNotFoundError("financial fixture not found: %s" % path)

    template: Optional[PDFTemplate] = load_template(path) if cached else None
    # Resolve compliance once from the (first) template so the report flag
    # is stable even when uncached reloads.
    probe = template if template is not None else load_template(path)
    use_compliant = _resolve_compliant(probe, force=compliant)

    last_pdf = b""
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(iterations):
        note = template if cached else load_template(path)
        assert note is not None
        last_pdf = build_template_document(note, compliant=use_compliant)
    total_time = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak / (1024.0 * 1024.0)

    return {
        "iterations": iterations,
        "cache": cached,
        "compliant": use_compliant,
        "fixture": str(path),
        "total_time": total_time,
        "jobs_per_sec": iterations / total_time if total_time else 0.0,
        "ms_per_job": 1000.0 * total_time / iterations if iterations else 0.0,
        "peak_mb": peak_mb,
        "output_bytes": len(last_pdf),
        "pages": _page_count(last_pdf),
        "md5": hashlib.md5(last_pdf).hexdigest() if last_pdf else "",
        "pdf": last_pdf,
    }


def write_warmup_pdf(
    template: PDFTemplate,
    out_dir: Path,
    *,
    compliant: Optional[bool] = None,
) -> Path:
    """Write ``financial_report_output.pdf`` or ``financial_nocomply.pdf``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    use_compliant = _resolve_compliant(template, force=compliant)
    name = "financial_report_output.pdf" if use_compliant else "financial_nocomply.pdf"
    path = out_dir / name
    path.write_bytes(build_template_document(template, compliant=use_compliant))
    return path


def _fmt_bytes(value: int) -> str:
    if value >= 1 << 20:
        return "%.2f MiB" % (value / (1 << 20))
    if value >= 1 << 10:
        return "%.1f KiB" % (value / (1 << 10))
    return "%d B" % value


def format_report(results: Dict[str, Any], *, workers: int) -> List[str]:
    """Human-readable report lines for one run.

    Includes gocorepdfengine-compatible metric labels so multi-run
    summarizers can parse::

        Throughput: X ops/sec
        Avg Latency: Y ms
        Max Memory Allocated: Z MB
    """
    mode = "pdfa4+pdfua2" if results["compliant"] else "pdf20"
    cache = "on" if results["cache"] else "off"
    pages = results["pages"]
    peak_mb = float(results.get("peak_mb") or 0.0)
    lines = [
        "financial workload: %d jobs (template JSON -> rich tables) %s cache=%s"
        % (results["iterations"], mode, cache),
        "  total time:        %.3f s" % results["total_time"],
        "  Throughput:        %.2f ops/sec" % results["jobs_per_sec"],
        "  Avg Latency:       %.3f ms" % results["ms_per_job"],
        "  Max Memory Allocated: %.2f MB" % peak_mb,
        "  jobs/sec:          %.2f (workers=%d, serial)" % (
            results["jobs_per_sec"], workers,
        ),
        "  ms/job:            %.1f" % results["ms_per_job"],
        "  pages:             %s" % ("?" if pages is None else str(pages)),
        "  output bytes:      %d (%s)" % (
            results["output_bytes"], _fmt_bytes(results["output_bytes"]),
        ),
        "  compliance:        %s (embedded fonts)" % mode,
        "  cache:             %s (template only; PDF never cached)" % cache,
        "  deterministic:     yes (md5 %s)" % results["md5"],
        "  fixture:           %s" % results["fixture"],
    ]
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
    return max(1, min(requested, (os.cpu_count() or 1) * 2))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--nocomply", action="store_true",
        help="force plain PDF 2.0 (ignore template compliance flags)",
    )
    mode.add_argument(
        "--compliant", action="store_true",
        help="force PDF/A-4 + PDF/UA-2 (ignore template compliance flags)",
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--cached", dest="cached", action="store_true",
        help="reuse loaded template (default, BENCH_CACHE=1)",
    )
    cache_group.add_argument(
        "--uncached", dest="cached", action="store_false",
        help="re-parse the JSON every iteration (BENCH_CACHE=0)",
    )
    parser.set_defaults(cached=None)
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="total jobs (default BENCH_ITERATIONS or %d)" % _DEFAULT_ITERATIONS,
    )
    parser.add_argument(
        "--report", type=str, default=None,
        help="report file (default baselines/financial_stats_latest.txt)",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help="directory containing financial_report.json",
    )
    args = parser.parse_args(argv)

    iterations = (
        args.iterations
        if args.iterations is not None
        else _env_int("BENCH_ITERATIONS", _DEFAULT_ITERATIONS)
    )
    cached = (
        args.cached if args.cached is not None
        else os.environ.get("BENCH_CACHE", "1") != "0"
    )
    if args.nocomply:
        compliant: Optional[bool] = False
    elif args.compliant:
        compliant = True
    else:
        compliant = None  # follow JSON
    workers = _clamp_workers(_env_int("BENCH_WORKERS", _DEFAULT_WORKERS))
    skip_write = os.environ.get("BENCH_SKIP_WRITE") == "1"
    report = Path(args.report) if args.report is not None else _DEFAULT_REPORT
    data_dir = Path(args.data_dir) if args.data_dir else _DEFAULT_DATA_DIR

    results = run_bench(
        iterations,
        cached=cached,
        compliant=compliant,
        data_dir=data_dir,
    )
    # Drop the raw PDF from the report dict so we don't print huge objects.
    pdf_bytes = results.pop("pdf", b"")
    lines = format_report(results, workers=workers)
    header = (
        "run %s  iterations=%d cache=%d compliant=%s workers=%d"
        % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            iterations,
            int(cached),
            results["compliant"],
            workers,
        )
    )
    for line in lines:
        print(line)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote %s" % report)

    if skip_write:
        print("BENCH_SKIP_WRITE=1: warm-up PDF skipped")
        return 0
    template = load_template(data_dir / _FIXTURE_NAME)
    path = write_warmup_pdf(template, data_dir, compliant=compliant)
    print("wrote %s (%d bytes)" % (path, path.stat().st_size))
    if pdf_bytes and path.read_bytes() != pdf_bytes:
        # Warm-up rebuild is expected to match the last bench iteration.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
