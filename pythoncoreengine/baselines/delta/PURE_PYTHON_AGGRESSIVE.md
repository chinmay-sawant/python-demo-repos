# Pure-Python aggressive push (no architecture change)

Goal band from dump analysis: **~80–150 ops/sec** on Zerodha mix, still
**full tag + encode** (PDF/A-4 + PDF/UA-2), **serial** jobs, **stdlib only**.

## What “pure Python” means here

Same pipeline and product contract:

- every job still builds a full PDF  
- structure tree + MCIDs still present  
- no process pool, no C extensions, no “drop compliance for speed” mode  

Only tighter Python: less re-measure, specialized encode paths, fewer allocs.

## Throughput (warm, **no** harness tracemalloc)

| Workload | Before dump-era (approx) | After first caches | **After pure aggressive** |
| --- | ---: | ---: | ---: |
| Zerodha mix 500 pure loop | ~10–15 ops/sec* | ~40 ops/sec | **~67 ops/sec** |
| HFT one note (avg) | ~1.2–1.8 s | ~0.30 s | **~0.21–0.22 s** |
| Retail one note | ~20 ms | ~2.5–8 ms | **~2.2 ms** |
| Financial warm ×N | ~1 s+/job (PNG) | ~14 ms | **~4–5 ms** (~200+ ops/sec alone) |

\*Harness with tracemalloc reports lower thr. (~11–15 ops/sec) — memory
tracing is not the pure engine cost. Prefer pure loop numbers for this study.

### Official harness (tracemalloc on) after pure aggressive

See `zerodha_stats_PURE_AGGRESSIVE.txt` / `financial_stats_PURE_AGGRESSIVE.txt`.

Typical: thr. ~14 ops/sec, HFT ~1.1 s/job, retail ~8 ms, peak ~38 MB.

## Code changes (this push)

| Area | Change |
| --- | --- |
| `layout.py` | Single-pass `_measure_row` (height + lines); no double wrap; single-line skip; pass height/lines into `_draw_row` |
| `structure.py` | Table cells with /Headers use fast shape; **RawPdfBody** TD/TH templates (no recursive encode_dict) |
| `write.py` | `RawPdfBody`, single-int array encode, tighter encode_dict |
| `content.py` | Fast `/TD`/`/TH` MCID BDC; slightly leaner CID text render |

## Gap to 80–150 ops/sec pure mix

| Target ops/sec | Need avg ms/job | Implied HFT budget (5% of mix) |
| ---: | ---: | ---: |
| 67 (now) | ~15 ms | ~0.21 s (matches measured) |
| **80** | 12.5 ms | ~**0.15 s** HFT |
| **150** | 6.7 ms | ~**0.08 s** HFT |

So pure aggressive is **in the approach band** (~67, near 80) but not mid-range 150.
Remaining wall on HFT is still **real work**: `_draw_row`, content operators,
zlib compress, per-cell structure objects (UA-2 requires them).

## Dump artifacts

- `benchmarks_reports/pythoncoreengine/dumps_pure/` — post-aggressive HFT topsites  
- Earlier: `dumps_v2/PATH_TO_1000OPS.md` for strategy context  

## Honest ceiling reminder

Further pure-Python wins are still available (batch grid ops, fewer content
calls, tighter number formatting) but **diminishing**. Crossing **150** pure
serial with full HFT tagging likely needs cutting *semantic* work (fewer
operators/objects), not just cleaner loops. **1000** remains architecture /
runtime territory.
