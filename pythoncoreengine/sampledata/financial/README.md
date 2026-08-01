# Financial-report benchmark (full-format template)

Pipeline (stdlib only, pure-Python engine):

```
financial_report.json  →  model.load_template  →  render.build_template_document  →  PDF
```

**Template field guide:** [guides/TEMPLATE_REFERENCE.md](../../guides/TEMPLATE_REFERENCE.md)

This is the full `config` / `title` / `table` / `elements` / `footer` JSON
shape (not the Zerodha domain `client`/`trades` fixtures). The sample
enables `arlingtonCompatible: true`, so the default render is
**PDF/A-4 + PDF/UA-2** with embedded fonts.

## Fixture

| File | Role |
|------|------|
| `financial_report.json` | Full-format template (title, 8 tables, 2 spacers, charts, footer) |
| `financial_report_output.pdf` | Warm-up output (compliant path) |
| `financial_nocomply.pdf` | Warm-up output (plain PDF 2.0) |

## Cache modes (template only)

| Env | Behavior |
|-----|----------|
| `BENCH_CACHE=1` (default) | Parse JSON **once**; reuse the template every iteration |
| `BENCH_CACHE=0` | Re-read + re-parse the JSON **every** iteration |

Both modes **generate a PDF every iteration** (layout + write). Cache only
skips template reload — PDF bytes are never cached.

## Compliance modes

| Flag | Behavior |
|------|----------|
| default | Honour JSON (`pdfaCompliant` / `arlingtonCompatible`) |
| `--compliant` | Force PDF/A-4 + PDF/UA-2 |
| `--nocomply` | Force plain PDF 2.0 |

## Make targets

```bash
make bench-financial                 # default = x10 multi-run + stats summary
make bench-financial-once            # single timed run
make bench-financial-nocomply        # single run, plain PDF 2.0
BENCH_CACHE=0 make bench-financial-once
make bench-financial-x2              # 2 runs + summary table
make bench-financial-x5              # 5 runs + summary
make bench-financial-x10             # 10 runs + summary (same as bench-financial)
make bench-financial-nocomply-x10
```

### Multi-run summary (matches zerodha baselines)

`make bench-financial` / `make bench-financial-x10` write:

| Path | Content |
|------|---------|
| `baselines/financial_bench_x10/financial_run{1..10}.txt` | Per-run logs |
| `baselines/financial_bench_x10_stats_latest.txt` | Aggregated summary |

Summary format (same shape as `zerodha_bench_x10_stats_latest.txt`):

```
# Financial x10 latest summary (pythoncoreengine harness)

Runs: 10
Best throughput: ... ops/sec
Worst throughput: ... ops/sec
Mean throughput: ... ops/sec
Median throughput: ... ops/sec
Stddev throughput: ... ops/sec
Mean avg latency: ... ms
Mean peak allocated: ... MB

| Run | Throughput | Avg latency | Peak allocated |
|-----|-----------:|------------:|---------------:|
| financial_run1.txt | ... | ... | ... |
...
```

## Env

| Variable | Default | Meaning |
|----------|---------|---------|
| `BENCH_ITERATIONS` | 100 | jobs |
| `BENCH_WORKERS` | 48 | workers (serial under GIL) |
| `BENCH_CACHE` | 1 | template cache |
| `BENCH_SKIP_WRITE` | — | `1` = skip warm-up PDF write |
| `BENCH_SEED` | 42 | reserved (parity with zerodha) |

## Package map

| Module | Role |
|--------|------|
| `engine.model` | `load_template` → `PDFTemplate` |
| `engine.layout` | `parse_props`, `RichTableLayout`, `StyledCell` |
| `engine.render` | `build_template_document` |
| `engine.bench_financial` | timed harness + report |
