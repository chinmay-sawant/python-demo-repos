# Path to ~1000 ops/sec — dump v2 findings (post-optimization)

Dumps under this directory (`dumps_v2/`), after the dump-driven perf commit.
Warm caches applied before profile (PNG / TTF process caches hot).

## Reality check

| Workload | Profile wall (warm) | Implied ops/sec (that workload alone) | Peak heap |
| --- | ---: | ---: | ---: |
| retail compliant | **0.008 s** | ~125 ops/sec | 0.40 MB |
| financial once (cached PNG) | **0.014 s** | ~70 ops/sec | 0.52 MB |
| financial ×10 | **0.147 s** | ~68 ops/sec | 1.77 MB |
| HFT **nocomply** | **0.53 s** | ~1.9 ops/sec | 6.7 MB |
| HFT **compliant** | **1.25 s** | ~0.8 ops/sec | 19.5 MB |
| Zerodha mix ×100 | **8.63 s** | **~11.6 ops/sec** | 36.4 MB |

**1000 ops/sec** needs ~**1 ms/job** average on the 80/15/5 mix.

With 5% HFT jobs: even if retail/active were free, HFT must be ≲ **~20 ms/job**
(today **~530 ms nocomply / ~1250 ms compliant** → **~25–60×** still needed on HFT alone).

So 1000 ops/sec in pure serial Python layout is **not realistic** without either:

1. **Massive HFT layout/encode reduction** (orders of magnitude), and/or  
2. **Multi-process job parallelism** (bypass GIL; N cores × single-job rate), and/or  
3. **Throughput bench mode** that drops compliance/tagging (and still needs HFT ≪ 50 ms).

Go-era ~1000+ ops/sec was a different runtime (no GIL, different engine).

---

## Zerodha / HFT — where time goes (compliant)

From `hft_once.cprofile_topsites.txt` + `hft_once.module_rollup.txt`:

| Module (self-time) | ~tottime | Role |
| --- | ---: | --- |
| **write.py** | **0.41 s** | `encode_dict` / `encode_value` / names — PDF object graph |
| **content.py** | **0.17 s** | operators, numbers, lines |
| **layout.py** | **0.12 s** | `_draw_row`, grid, wrap |
| **doc.py** | **0.09 s** | reserve/set_value/render |
| **structure.py** | **0.08 s** | `begin_cell`, element dicts |

### Compliance tax

| Mode | HFT wall | Ratio |
| --- | ---: | ---: |
| compliant (A-4 + UA-2) | 1.25 s | 1.0× |
| `--nocomply` | **0.53 s** | **0.42×** (~**2.4× faster**) |

Structure tree + tagged cells + encode of ~14k StructElems is the largest
*optional* cost. Nocomply still spends ~0.30 s in layout emit + content.

### Heap (compliant HFT)

From `hft_once.tracemalloc.txt` peak **19.5 MB**:

1. Final `bytearray(estimate)` ~2.6 MiB  
2. `encode_object` bodies ~2.3 MiB (14k objects)  
3. `StructElem(...)` ~1.3 MiB  
4. `headers = list(headers)` churn on StructElem  
5. `elem.kids = [mcid]` per cell  

---

## Financial — where time goes (warm)

PNG is **no longer** the multi-job bottleneck (cache works).  
`financial_x10` ~0.015 s/job:

| Area | Notes |
| --- | --- |
| `DocumentBuilder.render` / encode | Still largest share |
| `RichTableLayout._draw_row` / emit | Second |
| zlib.compress | ~0.01 s of ×10 total (small) |

Financial alone can approach **~70 ops/sec** warm — still **~14×** below 1000.

---

## Highest-leverage next moves (ordered)

### A. Toward higher mix ops/sec (practical)

1. **ProcessPoolExecutor job fan-out** in the harness (and optional library API)  
   - 24 cores × ~12 ops/sec serial ↛ linear, but **100–200+ ops/sec** is plausible if HFT workers don’t thrash RAM.  
   - Does **not** require 1 ms/job single-threaded.

2. **Optional throughput mode** for benches: `--nocomply` + skip structure  
   - HFT already **2.4×**; mix would jump if HFT share of wall drops.

3. **Reduce structure object count for uniform trade grids**  
   - Shared/static cell templates, batch TD encode, fewer reserved IDs.  
   - Directly attacks write.py + structure.py self-time on HFT.

4. **Faster content operator path**  
   - Pre-format number tokens for common coordinates; fewer genexpr joins.  
   - Large self-time in `content.py` even without compliance.

5. **Layout: skip redundant wrap/measure** for fixed-width numeric trade columns  
   - HFT rows are uniform; row height can be constant after first row.

### B. Toward 1000 ops/sec specifically

| Approach | Est. ceiling (rough) | Notes |
| --- | --- | --- |
| Serial compliant Python | **~15–30 ops/sec** | HFT floor |
| Serial nocomply + layout opts | **~30–80 ops/sec** | Still HFT-bound |
| Multi-process ×24 @ 15 ops/sec/worker | **~100–250 ops/sec** | RAM: 24 × HFT peak |
| Multi-process + nocomply + HFT 10× layout cut | **~500–1000 ops/sec?** | Stretch; needs major HFT rewrite |
| Native/extension core for layout+encode | **1000+** | Closest to Go-era |

### C. Do **not** expect 1000 from

- More micro-caches alone (retail already 8 ms; HFT is thousands of cells)  
- Thread pools inside one document (GIL; zlib already parallel)  
- Financial PNG work (already amortized)

---

## Recommended experiment sequence

1. Bench **Zerodha nocomply** 500-job mix → measure ops/sec delta vs compliant.  
2. Prototype **process pool** harness (`BENCH_WORKERS` as real processes).  
3. Profile HFT nocomply layout-only (`_layout_content` / `_draw_row`) for row-height constant-path.  
4. Structure batching for trade tables (compliant path).  
5. Re-dump and update `baselines/delta/DELTA.md`.

## Artifact index

| Stem | Purpose |
| --- | --- |
| `hft_once` | Compliant HFT CPU + heap |
| `hft_once_nocomply` | Compliance tax |
| `retail_once` | Light-path floor |
| `zerodha_mix_100` | Mix throughput under profile |
| `financial_once` / `financial_x10` | Warm financial |
| `*.module_rollup.txt` | Per-file self-time |
| `*.tracemalloc.txt` | Heap ranks |
