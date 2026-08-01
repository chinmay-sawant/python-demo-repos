# Benchmark report — pythoncoreengine (dump-driven)

Generated with skill `skills/benchmarks_reports/SKILLS.md`.

**Evidence rule for this report:** every metric and checklist claim is taken
from the captured **CPU profiles** (`cProfile` / `.pstats`) and **heap
snapshots** (`tracemalloc`) under `benchmarks_reports/pythoncoreengine/dumps/`.
Harness throughput logs are **not** used as primary evidence here.

Every checklist snippet starts with `[ ]` so reviewers can tick items after
verifying the dump claim or landing a fix.

## Run metadata

```yaml
timestamp: 2026-08-01T13:22:38Z
repository: codehound-python-perf-targets
repository_path: /home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets
branch: chore/pythoncoreengine-benchmark-reports
commit: c29eb3df9e72856ce396b373249318bb857ac874
bench_target: /home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/pythoncoreengine
report_path: ./benchmarks_reports/pythoncoreengine/2026-08-01-chore-pythoncoreengine-benchmark-reports.md
dump_path: ./benchmarks_reports/pythoncoreengine/dumps/
baseline_path: pythoncoreengine/baselines/   # not primary for this report
```

## Dump evidence

- Build / env: Python 3.13.10, stdlib `cProfile` + `tracemalloc` (25 traceback frames)
- Capture: one profiled call and one separate heap-traced call per stem (heap run is **not** under cProfile)
- Dump stems:
  - `hft_once` — one compliant HFT contract note (2000 trades)
  - `retail_once` — one compliant retail note
  - `zerodha_mix_20` — 20 jobs of the 80/15/5 mix (seed 42)
  - `financial_once` — one full-format financial template (compliant)
  - `dense_table_once` — 2000×8 tagged dense table (PDF/A-4 + PDF/UA-2)
- Artifact kinds per stem:
  - `*.pstats` — raw cProfile binary
  - `*.cprofile_cumtime.txt` / `*.cprofile_tottime.txt` — top-40 text
  - `*.cprofile_topsites.txt` — top-50 call sites with file:line
  - `*.tracemalloc.txt` — heap snapshot (traceback + lineno ranks)
  - `*.meta.txt` — wall seconds + peak/current MB
- Index: `benchmarks_reports/pythoncoreengine/dumps/INDEX.md`

## Generation checklist

- [x] Captured cProfile + tracemalloc dumps for each planned stem.
- [x] Recorded machine / Python / commit for the dump set.
- [x] Linked every snippet to dump paths and concrete dump numbers.
- [x] Copied the smallest source excerpt at the dump hot site.
- [x] Wrote description + improvement path from dump evidence only.
- [x] Added **Improved version** only when a safer pattern is clear from the dump site.
- [x] Left every snippet checkbox as `[ ]` until a human verifies the claim.
- [x] Ran `git diff --check` after writing the report.

---

## Results summary (from dumps only)

| Stem | Profile wall (cProfile) | Heap-run wall (tracemalloc on) | Peak heap | Primary dump files |
| --- | ---: | ---: | ---: | --- |
| `retail_once` | 0.017 s | 0.103 s | 0.92 MB | `retail_once.meta.txt` |
| `zerodha_mix_20` | 0.374 s | 2.499 s | 4.75 MB | `zerodha_mix_20.meta.txt` |
| `financial_once` | **1.083 s** | 51.644 s* | 8.07 MB | `financial_once.meta.txt` |
| `hft_once` | **1.840 s** | 8.096 s | 28.67 MB | `hft_once.meta.txt` |
| `dense_table_once` | **2.103 s** | 7.913 s | 36.21 MB | `dense_table_once.meta.txt` |

\*Heap-run wall under continuous `tracemalloc` is **not** a CPU estimate; it
shows allocator tracing overhead (especially on the PNG path). Use
`profile_wall_s` for CPU ranking.

### Dump-derived cost split (cProfile cumtime, top of each stem)

| Stem | Dominant call site | cumtime | Share of profile wall |
| --- | --- | ---: | ---: |
| `financial_once` | `image.py:186(decode_png)` | 1.043 s | ~96% of 1.083 s |
| `hft_once` | `layout.py:730(emit)` / `_layout_content` | 0.714 s | ~39% of 1.840 s |
| `hft_once` | `write.py:350(encode_value)` (under `doc.render`) | 0.701 s | ~38% of 1.840 s |
| `dense_table_once` | `write.py:350(encode_value)` | 0.883 s | ~42% of 2.103 s |
| `dense_table_once` | `layout.py:730(emit)` | 0.750 s | ~36% of 2.103 s |
| `zerodha_mix_20` | `font.py:1121(generate_subsets)` (+ TTF parse) | ~0.089 s | ~24% of 0.374 s |

---

## Checklist snippets

### [ ] financial-png-decode — pure-Python PNG filter path owns financial CPU

- Metric (CPU dump): `decode_png` cumtime **1.043 s** / tottime **0.610 s** of profile wall **1.083 s**; `_paeth_predictor` cumtime **0.346 s**, **592 000** calls; `abs` **1 776 000** calls (tottime 0.115 s)
- Metric (heap dump): peak **8.07 MB**; largest retained block ~**2227 KiB** under `layout.emit` → image layout (`financial_once.tracemalloc.txt` rank 1)
- Source: `pythoncoreengine/engine/image.py:173` / `image.py:186`
- Checklist theme: image decode / pure-Python hot loop
- Related dump: `dumps/financial_once.cprofile_topsites.txt`, `dumps/financial_once.cprofile_tottime.txt`, `dumps/financial_once.tracemalloc.txt`

**Description:**  
On the financial template, cProfile attributes almost the entire document cost to decoding two PNGs. The Paeth filter path is a per-byte Python loop (`_paeth_predictor` + scanline reconstruction), not table layout or PDF encoding.

**How this can be improved:**  
Cache decoded image bytes per fixture/hash across jobs; or pass through already-deflated IDAT when the PDF only needs an XObject; or replace the pure-Python filter loop with a faster decoder (stdlib-limited: precompute fixtures as raw/Flate, or optional C extension). Avoid leaving `tracemalloc` on for production benches — heap-run wall was **51.6 s** for one job vs **1.08 s** profiled.

Current snippet:

```python
# engine/image.py — dump hot sites
def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    # ...

def decode_png(data: bytes) -> PNGImage:
    # ...
    for _ in range(height):
        filter_type = raw[pos]
        # ...
        for index in range(stride):
            # ...
            elif filter_type == 4:
                value += _paeth_predictor(left, up, up_left)
```

Improved version:

```python
# Conceptual: cache decoded pixels so build_template_document does not
# re-filter the same PNG every job (PDF bytes still regenerated).
_PNG_CACHE: dict[bytes, PNGImage] = {}

def decode_png(data: bytes) -> PNGImage:
    cached = _PNG_CACHE.get(data)
    if cached is not None:
        return cached
    image = _decode_png_uncached(data)
    _PNG_CACHE[data] = image
    return image
```

**Expected impact:** Dump implies up to ~**1.0 s/job** recoverable on financial once if decode is skipped/cached (≈96% of profiled wall). Unmeasured after fix.

---

### [ ] pdf-object-encode — recursive `encode_value` dominates large-doc render CPU

- Metric (CPU dump): HFT `encode_value` tottime **0.187 s**, cumtime **0.701 s**, **190 230** calls; `encode_dict` cumtime **0.674 s** / **29 885** calls; `isinstance` **1 517 061** calls (tottime **0.142 s**). Dense table: `encode_value` cumtime **0.883 s**, `isinstance` **1 954 882** calls (tottime **0.173 s**)
- Source: `pythoncoreengine/engine/write.py:350` / `write.py:339`
- Checklist theme: buffer / xref pooling (encode path) · PDF object graph cost
- Related dump: `dumps/hft_once.cprofile_tottime.txt`, `dumps/dense_table_once.cprofile_topsites.txt`

**Description:**  
After layout, `Document.render` materializes the object graph through recursive PDF syntax encoding. Dumps show `encode_value` / `encode_dict` / `encode_array` plus massive `isinstance` dispatch as the largest **self-time** cluster on HFT and dense tagged tables — not zlib.

**How this can be improved:**  
Pre-encode stable sub-dicts (font descriptors, colorspace, ProcSet); reduce dict nesting depth for structure leaves; specialize fast paths for common types before the general `isinstance` chain; reuse encoded object bodies when only page content changes (harder under deterministic full rebuild).

Current snippet:

```python
# engine/write.py — dump hot sites
def encode_dict(pairs: Dict[Any, Any]) -> bytes:
    chunks: List[bytes] = []
    for key, value in pairs.items():
        chunks.append(encode_name(key))
        chunks.append(encode_value(value))
    return b"<< " + b" ".join(chunks) + b" >>"

def encode_value(value: Any) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, ObjectId):
        return value.render_ref()
    # ... many isinstance branches ...
```

**Expected impact:** Dump-relative: encoding cluster is ~**0.7–0.9 s** cumtime on large docs. Partial specialization unmeasured.

---

### [ ] table-draw-row — per-row layout/text work is second CPU pillar on HFT/dense

- Metric (CPU dump): HFT `_draw_row` cumtime **0.453 s** / **2047** calls; `wrap_text` **0.215 s** / **36 726** calls; `_row_height` **0.161 s**. Dense: `_draw_row` **0.461 s**, `wrap_text` **0.244 s**, `_row_height` **0.182 s`
- Source: `pythoncoreengine/engine/layout.py:782` / `layout.py:182` / `layout.py:712`
- Checklist theme: dense table layout · HFT multipage scale
- Related dump: `dumps/hft_once.cprofile_topsites.txt`, `dumps/dense_table_once.cprofile_topsites.txt`

**Description:**  
Table emission (`layout.emit` → `_draw_row`) is the layout half of large documents. Dumps show thousands of row draws each doing wrap/width measurement (`text_width` / `_width_of_char` gens) before content operators are appended.

**How this can be improved:**  
For uniform trade grids, cache per-column wrap results and fixed row heights; skip re-measure when font/size/width unchanged; batch cell text that shares style; reduce genexpr/`sum` width paths seen in tottime dumps.

Current snippet:

```python
# engine/layout.py — dump hot sites
def _draw_row(self, flow, cells, y_top, size, column_x, x1, row_elem=None, row_index=-1):
    """Draw one row band: cell backgrounds, wrapped text, cell outlines."""
    # backgrounds, wrap_text per cell, structure begin_cell, content ops...
```

**Expected impact:** Dump-relative: ~**0.45 s** cumtime in `_draw_row` alone on HFT/dense once. Row-height cache unmeasured.

---

### [ ] structure-cell-heap — tagged cells allocate large StructElem graphs

- Metric (heap dump): dense table rank 2 — **3373 KiB** across **31 984** allocs at `structure.begin_cell` → `_reserve_value` / `_element_dict`; rank 4 — **1749 KiB** / **15 993** `StructElem(` constructions. HFT peak **28.67 MB** with large layout.table + encode_object bodies
- Source: `pythoncoreengine/engine/structure.py` (via `layout.py:866` `manager.begin_cell`)
- Checklist theme: structure hot path · buffer / allocation
- Related dump: `dumps/dense_table_once.tracemalloc.txt` ranks 2–4, `dumps/hft_once.tracemalloc.txt`

**Description:**  
With PDF/UA-2 tagging, every cell reserves structure objects. The heap snapshot pins multi-megabyte retained cost to `begin_cell` / `StructElem` / deferred `_element_dict`, separate from the final PDF bytearray.

**How this can be improved:**  
Continue phase-6 style: batch TD leaf serialization, arena/pool StructElem, pre-reserve ParentTree/MCID capacity per page stripe, share static dict templates for leaf cells.

Current snippet:

```python
# Call chain from dump traceback (dense_table_once.tracemalloc rank 2/4):
# layout._draw_row → manager.begin_cell → StructElem(...) / _reserve_value(_element_dict)
cell_elem, mcid = manager.begin_cell(...)
```

**Expected impact:** Dump shows multi-MiB structure graph on dense tagged tables; CPU for `structure.begin_cell` on HFT is smaller than encode/layout (cumtime **0.068 s**) but heap pressure is first-class for peak MB.

---

### [ ] final-pdf-buffer — peak heap includes large final `bytearray(estimate)`

- Metric (heap dump): HFT rank 1 — **2688.5 KiB** at `doc.py` buffer allocation during `Document.render`; dense rank 1 — **3454.1 KiB** at `buffer = bytearray(estimate)`; dense peak **36.21 MB**, HFT peak **28.67 MB**
- Source: `pythoncoreengine/engine/doc.py:248` (from dump traceback)
- Checklist theme: buffer / xref pooling
- Related dump: `dumps/hft_once.tracemalloc.txt` rank 1, `dumps/dense_table_once.tracemalloc.txt` rank 1

**Description:**  
The heap snapshot’s largest single allocation sites for large docs are the final PDF writer buffer and encoded object bodies (`set_value` → `encode_object`). Pooling already reuses the buffer across renders on the same builder; a **single** large render still needs ~output-sized contiguous bytes.

**How this can be improved:**  
Streaming write to a file/socket for huge docs (avoid holding full PDF in RAM); tighter estimate to reduce over-alloc; release intermediate page operator lists earlier after compress. Pooling helps multi-job builders, not single-shot peak.

Current snippet:

```python
# engine/doc.py — dump traceback site
estimate = sum(len(body) for _number, body in self._objects)
estimate += size * 40 + 256
buffer = self._pooled_buffer
if buffer is None or len(buffer) < estimate:
    buffer = bytearray(estimate)
```

**Expected impact:** Dump-relative: ~**2.5–3.4 MiB** in the final buffer alone on HFT/dense; total peak is much larger due to structure + page graphs.

---

### [ ] mix-font-reload — small-doc mix spends significant time re-parsing fonts

- Metric (CPU dump): `zerodha_mix_20` — `generate_subsets` cumtime **0.089 s**, `font.from_file` / `ttf` / `_unpack` cluster ~**0.08 s** of **0.374 s** total; **40** `from_file` calls for 20 jobs
- Source: `pythoncoreengine/engine/font.py:1121` / `font.py:209`
- Checklist theme: fonts & color · model / template cache boundary
- Related dump: `dumps/zerodha_mix_20.cprofile_topsites.txt`, `dumps/zerodha_mix_20.meta.txt`

**Description:**  
On a 20-job mix dominated by small notes, cProfile shows font subset generation and TTF parse as a large fraction of time — larger relative share than on a single HFT doc (where layout+encode dominate). Retail alone is only **0.017 s** profile wall, so repeated font work is visible when many tiny PDFs are built.

**How this can be improved:**  
Process-level font face / subset cache keyed by (face, glyph set); reuse `DocumentBuilder` registries across jobs in the harness; avoid re-reading TTF files per document when glyphs are stable.

Current snippet:

```python
# dump sites (zerodha_mix_20 topsites):
# font.py:1121(generate_subsets)  cumtime ~0.089s  ncalls=20
# font.py:209(from_file)          cumtime ~0.080s  ncalls=40
```

**Expected impact:** Dump-relative: up to ~**24%** of short-mix wall if font load/subset is fully cached across jobs. Unmeasured after fix.

---

### [ ] parallel-compress-wait — page compress pool shows join time on multipage docs

- Metric (CPU dump): HFT `_render_page_streams` cumtime **0.275 s**; `ThreadPoolExecutor` shutdown/join cluster **~0.275 s**; dense similar **~0.290 s**. Not pure zlib self-time (zlib appears small in financial; multipage waits on worker completion)
- Source: `pythoncoreengine/engine/doc.py:829`
- Checklist theme: compression share
- Related dump: `dumps/hft_once.cprofile_topsites.txt`, `dumps/dense_table_once.cprofile_topsites.txt`

**Description:**  
Dumps confirm multipage renders enter the parallel compress path (thread bootstrap + `result`/`join`). Cumulative time under `_render_page_streams` is real wall on the main thread waiting for workers, smaller than encode+layout but non-zero on 39–50+ page docs.

**How this can be improved:**  
Already parallel. Optional: keep a long-lived pool to avoid per-document thread start; skip pool when page count is low (already skips `< 2` streams). Do not expect this to fix financial (PNG-bound) or encode-bound HFT alone.

Current snippet:

```python
# engine/doc.py — dump site
def _render_page_streams(self) -> None:
    # ...
    with ThreadPoolExecutor(max_workers=workers) as executor:
        self._compressed_pages = list(executor.map(task, range(len(streams))))
```

**Expected impact:** Dump-relative: ~**0.27–0.29 s** wait cluster on large multipage once. Secondary vs encode/layout/PNG.

---

### [ ] retail-vs-hft — scale confirmed by dump wall, not only tier logs

- Metric (CPU dump): `retail_once` profile wall **0.017 s**, peak **0.92 MB** vs `hft_once` **1.840 s**, peak **28.67 MB** (~**110×** wall, ~**31×** peak)
- Source: dump metas only
- Checklist theme: HFT multipage scale
- Related dump: `dumps/retail_once.meta.txt`, `dumps/hft_once.meta.txt`, topsites pair

**Description:**  
Side-by-side dump metas prove the HFT document is two orders of magnitude heavier than retail under the same compliant builder. Topsites differ in **kind** (retail is too short for the encode/layout pillars to dominate the same way; HFT shows encode + `_draw_row` clearly).

**How this can be improved:**  
Prioritize HFT/dense dump topsites (`encode_value`, `_draw_row`, structure heap) over retail micro-optimizations. Use retail dumps as a regression floor (should stay ≪ 50 ms profile wall on this machine class).

Current snippet:

```text
# from dumps/*.meta.txt
retail_once: profile_wall_s=0.016773  peak_mb=0.9186
hft_once:    profile_wall_s=1.840303  peak_mb=28.6745
```

**Expected impact:** Sets prioritization only; no direct code change.

---

## Optional: dump comparison block

| Dimension | retail_once | hft_once | financial_once | dense_table_once |
| --- | ---: | ---: | ---: | ---: |
| Profile wall | 0.017 s | 1.840 s | 1.083 s | 2.103 s |
| Peak heap | 0.92 MB | 28.67 MB | 8.07 MB | 36.21 MB |
| #1 CPU (dump) | (short) | encode + layout emit | `decode_png` | encode + layout emit |
| #1 heap rank | (small) | final buffer + trade table | image layout buffer | final buffer + begin_cell |

---

## Final evidence

- Branch / commit: `chore/pythoncoreengine-benchmark-reports` @ `c29eb3df9e72856ce396b373249318bb857ac874`
- Dump directory: `benchmarks_reports/pythoncoreengine/dumps/`
  - `hft_once.*`, `retail_once.*`, `zerodha_mix_20.*`, `financial_once.*`, `dense_table_once.*`
  - `INDEX.md`
- Report file: `benchmarks_reports/pythoncoreengine/2026-08-01-chore-pythoncoreengine-benchmark-reports.md`
- Skill: `skills/benchmarks_reports/SKILLS.md`
- Validation: `git diff --check` — pass
- Reviewer notes: Snippet checkboxes left `[ ]`. All numbers quoted above appear in the named dump files; re-open those files to audit.
