"""Benchmark harness for phase 6 (performance & pooling) -- stdlib only.

Renders a phase5_table_multipage-like dense compliant document (PDF/A-4 +
PDF/UA-2, tagged table) and reports total time, per-page average, output
bytes, peak heap (tracemalloc) and the compression-time share.  Run from
the project root::

    python3 -m engine.bench [--rows 2000] [--cols 8] [--report baselines/bench_python.txt]

The final numbers are appended to the report file (default
``baselines/bench_python.txt`` relative to the repository root) so before/
after optimisation runs can be compared.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import time
import tracemalloc
from pathlib import Path
from typing import Dict, List, Optional

import engine.doc as engine_doc
from .doc import DocumentBuilder
from .layout import PageMargins, TableLayout

_DEFAULT_ROWS = 2000
_DEFAULT_COLS = 8

_FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_PRODUCTS = ["Widget", "Gadget", "Sprocket", "Bolt", "Cable", "Flange", "Nut", "Washer"]
_REGIONS = ["EU", "US", "APAC", "LATAM"]
_STATUS = ["active", "hold", "shipped", "backlog"]


def dense_table_rows(n_rows: int, n_cols: int) -> List[List[str]]:
    """Deterministic dense table rows with ``n_cols`` columns."""
    headers = ["SKU", "Product", "Category", "Price", "Stock", "Region", "Qty", "Status"]
    rows: List[List[str]] = []
    for row in range(n_rows):
        rows.append(
            [
                "SKU-%04d" % row,
                _PRODUCTS[row % len(_PRODUCTS)],
                "A" if row % 2 == 0 else "B",
                "%.2f" % (9.99 + (row % 500) * 1.5),
                str(100 + (row % 900)),
                _REGIONS[row % len(_REGIONS)],
                str((row * 7) % 500),
                _STATUS[row % len(_STATUS)],
            ][:n_cols]
        )
    return rows


def dense_col_widths(n_cols: int) -> List[float]:
    """Column widths summing to <= the A4 content width at 48pt margins."""
    if n_cols == 5:
        return [60, 90, 90, 90, 60]
    base = [60.0] * n_cols
    total = sum(base)
    while total > 499.0:
        for index in range(n_cols - 1, -1, -1):
            if total <= 499.0:
                break
            base[index] = round(base[index] - 1.0, 1)
            total = sum(base)
    base[-1] = round(base[-1] - (total - 499.0), 1)
    return base


def render_dense_document(
    n_rows: int = _DEFAULT_ROWS, n_cols: int = _DEFAULT_COLS
) -> DocumentBuilder:
    """Build (not yet render) the dense tagged table document."""
    widths = dense_col_widths(n_cols)
    builder = DocumentBuilder(
        margins=PageMargins(48, 48, 48, 48),
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Phase-6 dense table bench",
    )
    flow = builder.flow()
    flow.table(
        TableLayout(
            col_widths=widths,
            header=dense_table_rows(0, n_cols) or [
                "SKU", "Product", "Category", "Price", "Stock", "Region", "Qty", "Status"
            ][:n_cols],
            rows=dense_table_rows(n_rows, n_cols),
            size=9,
        )
    )
    return builder


def _measure_compression(builder: DocumentBuilder) -> float:
    """Time spent inside zlib.compress during builder.render() (share only).

    Run on a serial-compression builder (``parallel_compress=False``):
    with the parallel path the per-call timing is polluted by GIL-reacquire
    waits inside ``zlib.compress``, which would overstate the compression
    share several-fold.
    """
    original = engine_doc.compressed_stream
    elapsed = [0.0]

    def timed(data: bytes) -> bytes:
        start = time.perf_counter()
        out = original(data)
        elapsed[0] += time.perf_counter() - start
        return out

    engine_doc.compressed_stream = timed
    try:
        builder.render()
    finally:
        engine_doc.compressed_stream = original
    return elapsed[0]


def run_bench(n_rows: int, n_cols: int) -> Dict[str, object]:
    """One timed run; returns the measurement dict."""
    results: Dict[str, object] = {}
    start = time.perf_counter()
    builder = render_dense_document(n_rows, n_cols)
    layout_time = time.perf_counter() - start
    results["layout_time"] = layout_time
    results["pages"] = builder.flow().page_count

    serial_builder = render_dense_document(n_rows, n_cols)
    serial_builder.parallel_compress = False
    start = time.perf_counter()
    compress_share = _measure_compression(serial_builder)
    results["serial_render_time"] = time.perf_counter() - start
    results["compress_time"] = compress_share

    start = time.perf_counter()
    data = builder.render()
    results["render_time"] = time.perf_counter() - start
    results["bytes"] = len(data)
    results["md5"] = hashlib.md5(data).hexdigest()

    start = time.perf_counter()
    data_again = render_dense_document(n_rows, n_cols).render()
    results["repeat_render_time"] = time.perf_counter() - start
    results["deterministic"] = data_again == data

    tracemalloc.start()
    try:
        render_dense_document(n_rows, n_cols).render()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    results["peak_memory"] = peak
    return results


def _fmt_seconds(value: float) -> str:
    return "%.3f" % value


def _fmt_bytes(value: int) -> str:
    if value >= 1 << 20:
        return "%.2f MiB" % (value / (1 << 20))
    if value >= 1 << 10:
        return "%.1f KiB" % (value / (1 << 10))
    return "%d B" % value


def format_report(results: Dict[str, object], *, n_rows: int, n_cols: int) -> List[str]:
    """Human-readable report lines for one run."""
    pages = int(results["pages"])
    render_time = float(results["render_time"])
    lines = [
        "dense table: %d rows x %d cols (mode_pdfa4 + mode_pdfua2, tagged)" % (n_rows, n_cols),
        "  layout time:        %s s" % _fmt_seconds(float(results["layout_time"])),
        "  render time:        %s s (parallel_compress default)"
        % _fmt_seconds(render_time),
        "  render time:        %s s (parallel_compress=False)"
        % _fmt_seconds(float(results["serial_render_time"])),
        "  compress share:     %s s (%.1f%% of serial render)" % (
            _fmt_seconds(float(results["compress_time"])),
            100.0
            * float(results["compress_time"])
            / float(results["serial_render_time"])
            if float(results["serial_render_time"])
            else 0.0,
        ),
        "  pages:              %d (%.1f ms/page avg)" % (
            pages, 1000.0 * render_time / pages if pages else 0.0,
        ),
        "  output bytes:       %d (%s)"
        % (int(results["bytes"]), _fmt_bytes(int(results["bytes"]))),
        "  peak heap (tracemalloc): %s" % _fmt_bytes(int(results["peak_memory"])),
        "  deterministic:      %s (md5 %s)"
        % ("yes" if results["deterministic"] else "NO", results["md5"]),
    ]
    return lines


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=_DEFAULT_ROWS)
    parser.add_argument("--cols", type=int, default=_DEFAULT_COLS)
    parser.add_argument(
        "--report",
        type=str,
        default=str(_repo_root() / "baselines" / "bench_python.txt"),
        help="report file to append to (default baselines/bench_python.txt)",
    )
    args = parser.parse_args(argv)

    results = run_bench(args.rows, args.cols)
    lines = format_report(results, n_rows=args.rows, n_cols=args.cols)
    header = "run %s  rows=%d cols=%d" % (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        args.rows,
        args.cols,
    )
    for line in lines:
        print(line)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("a", encoding="utf-8") as handle:
        handle.write(header + "\n")
        for line in lines:
            handle.write(line + "\n")
        handle.write("\n")
    print("\nappended to %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
