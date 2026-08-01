# Zerodha-style benchmark (pythoncoreengine)

Pure-Python engine only (no Go / gopdfsuit). Pipeline:

**Full template field guide:** [guides/TEMPLATE_REFERENCE.md](../../guides/TEMPLATE_REFERENCE.md)  
**Plan:** [plans/phase-08-zerodha-benchmark.md](../../plans/phase-08-zerodha-benchmark.md)

These JSON files are **domain** fixtures (`client` / `trades`), not the full
`config`/`elements` tree. Render maps them into layout tables that match the
colors/structure described in the template reference (same as gocorepdfengine).

```
JSON fixtures  →  model.ContractNote (+ expand_trades)  →  theme tables  →  PDF
```

## Fixtures (3 JSON files)

| File | Tier | Mix | Trades |
|------|------|-----|--------|
| `retail_investor.json` | retail | 80% | from JSON (2) |
| `active_trader.json` | active | 15% | expanded to **40** at runtime |
| `hft_algo.json` | hft | 5% | expanded to **2000** at runtime |

Warm-up PDFs written at the end of a run (unless `BENCH_SKIP_WRITE=1`):

| Output | Mode |
|--------|------|
| `zerodha_retail_output.pdf` | PDF/A-4 + PDF/UA-2 |
| `zerodha_active_output.pdf` | PDF/A-4 + PDF/UA-2 |
| `zerodha_hft_output.pdf` | PDF/A-4 + PDF/UA-2 |
| `zerodha_*_nocomply_output.pdf` | plain PDF 2.0 (`--nocomply`) |

## Cache modes (model / template only)

| Env | Behavior |
|-----|----------|
| `BENCH_CACHE=1` (default) | Expand trades **once**; reuse the 3 models every job |
| `BENCH_CACHE=0` | Re-expand trades + rebuild model **every** job |

Both modes still **generate a PDF every job** (layout + write). Cache only
skips model rebuild — PDF bytes are never cached.

## Compliance modes

| Flag | Behavior |
|------|----------|
| default | PDF/A-4 + PDF/UA-2, embedded fonts |
| `--nocomply` | plain PDF 2.0 only (fonts still embedded) |

## Make targets

```bash
make bench-zerodha                 # single run, compliant, model cache ON
make bench-zerodha-nocomply
BENCH_CACHE=0 make bench-zerodha   # uncached model path
make bench-zerodha-x2              # 2 runs + summary
make bench-zerodha-x5              # 5 runs + summary
make bench-zerodha-x10             # 10 runs + summary
make bench-zerodha-nocomply-x10
```

### Multi-run summary

| Path | Content |
|------|---------|
| `baselines/zerodha_bench_x10/zerodha_run{1..10}.txt` | Per-run logs |
| `baselines/zerodha_bench_x10_stats_latest.txt` | Aggregated summary |

```
# Zerodha x10 latest summary (pythoncoreengine harness)

Runs: 10
Best throughput: ... ops/sec
...
| Run | Throughput | Avg latency | Peak allocated |
```

## Env

| Variable | Default | Meaning |
|----------|---------|---------|
| `BENCH_ITERATIONS` | 500 | jobs (Go used 5000; Python default is lower) |
| `BENCH_WORKERS` | 48 | workers (serial under GIL; env kept for parity) |
| `BENCH_CACHE` | 1 | model cache (template-only; PDF never cached) |
| `BENCH_SKIP_WRITE` | — | `1` = skip warm-up PDF files |
| `BENCH_SEED` | 42 | expand + schedule seed |

## Package map

| Module | Role |
|--------|------|
| `engine.model` | JSON → `ContractNote`, `expand_trades` |
| `engine.color` | hex parse + Zerodha theme colors |
| `engine.layout` | tables, fills, borders, multipage flow |
| `engine.render` | note → layout → `build_document` |
| `engine.bench_zerodha` | timed harness + warm-up PDF write |
