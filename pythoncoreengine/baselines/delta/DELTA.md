# Benchmark delta — pre-optimization vs post-optimization

Captured on branch `chore/pythoncoreengine-benchmark-reports`.

| File | Meaning |
| --- | --- |
| `*_ORIGINAL.txt` | Pre-optimization harness logs (from commit before engine perf fixes) |
| `*_OPTIMIZED.txt` | After dump-driven fixes (PNG/font/encode/wrap/structure/buffer/compress pool) |
| `*_stats_latest.txt` (parent dir) | Points at the **latest** optimized run |

Target stretch goal: **~1000 ops/sec** on Zerodha mix (Go-era ballpark). Current pure-Python path is still serial under the GIL; HFT (~5% of jobs, ~1.4 s/job) dominates wall time.

## Zerodha — 500 jobs, PDF/A-4+UA-2, model cache on

| Metric | ORIGINAL | OPTIMIZED | Delta |
| --- | ---: | ---: | ---: |
| Throughput | 10.21 ops/sec | **10.69 ops/sec** | **+5%** |
| Avg latency | 97.9 ms | **93.6 ms** | **−4%** |
| Peak memory | 60.38 MB | **40.25 MB** | **−33%** |
| Retail ms/job | 19.3 | **8.8** | **−54%** |
| Active ms/job | 42.8 | **37.2** | **−13%** |
| HFT ms/job | 1357.2 | 1442.3 | ~flat (noise; still the bottleneck) |

### Zerodha x2 @ 50 iterations

| Metric | ORIGINAL | OPTIMIZED | Delta |
| --- | ---: | ---: | ---: |
| Mean throughput | 12.49 ops/sec | **15.59 ops/sec** | **+25%** |
| Mean peak memory | 34.29 MB | **24.28 MB** | **−29%** |

## Financial — 3 jobs, PDF/A-4+UA-2, template cache on

| Metric | ORIGINAL | OPTIMIZED | Delta |
| --- | ---: | ---: | ---: |
| Throughput | 0.07 ops/sec | **0.21 ops/sec** | **~3×** |
| Avg latency | 13379.6 ms | **4780.4 ms** | **−64%** |
| Peak memory | 14.65 MB | **8.12 MB** | **−45%** |

## Gap to 1000 ops/sec (Zerodha)

- Need avg **~1 ms/job** (1000 ops/sec).
- With 5% HFT, even zero-cost retail/active implies HFT ≲ **~20 ms/job** (today ~**1400 ms** → **~70×** on HFT alone).
- Realistic next levers: multipage layout cost, structure-tree/encode graph, process-pool parallelism across jobs (GIL bypass), optional non-compliant fast path for throughput benches.

## Fixes included in OPTIMIZED

1. Process-level PNG decode + Flate XObject cache  
2. Process-level TTF `from_file` cache  
3. `encode_value` exact-type hot path  
4. Faster `text_width` + `wrap_text` LRU  
5. Structure `begin_cell` / leaf kids reuse  
6. Release layout state before final PDF buffer  
7. Process-global parallel zlib pool (≥4 pages)  
