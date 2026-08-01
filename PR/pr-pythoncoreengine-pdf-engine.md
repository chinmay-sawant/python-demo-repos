## Summary

Ship the pure-Python PDF engine (`pythoncoreengine`) on this branch: PDF 2.0 writer with PDF/A-4 + PDF/UA-2 compliance, Zerodha contract-note and full-format financial template renderers, and multi-run benchmarks with **model/template-only caching** (PDF never cached). Also fix table cell text that was sitting on the top border.

---

## Motivation / context

- Plans: `pythoncoreengine/plans/` (phases 01–08, base PDF/A-4 + PDF/UA-2 plan)
- Sample parity with `gocorepdfengine` Zerodha + financial workloads
- Issues: see **Related issues**

---

## Changes

### Core PDF engine

- Pure-Python PDF 2.0 assembly (content, fonts/subsetting, images, structure tree, XMP/ICC, encryption hooks)
- Document builder + page flow + tagged tables for PDF/A-4 + PDF/UA-2
- Layout primitives: margins, wrap, multipage tables, rich per-cell template tables

### Zerodha benchmarks (3 JSON fixtures)

- `retail_investor.json` / `active_trader.json` / `hft_algo.json` → `ContractNote` → theme tables → PDF
- 80/15/5 workload mix; `BENCH_CACHE` reuses expanded models only (PDF regenerated every job)
- Warm-up PDFs: `zerodha_{retail,active,hft}_output.pdf`
- Multi-run scripts + Make targets (`bench-zerodha`, x2/x5/x10, nocomply) with Go-style throughput summary

### Financial report benchmarks

- Full-format `financial_report.json` → `PDFTemplate` → `RichTableLayout` → PDF
- Template-only cache; compliance from JSON (`arlingtonCompatible`) or `--nocomply` / `--compliant`
- Multi-run harness + `summarize_runs.py` + Make targets mirroring Zerodha

### Layout fix

- Vertically centre cell text using the PDF text baseline (was drawn at top padding, colliding with borders)
- Applied to `TableLayout` and `RichTableLayout`; regression test in `test_table.py`

---

## Impact

| Area | Impact |
|------|--------|
| **Performance** | Serial GIL-bound layout; defaults scaled (Zerodha 500 / financial 100 iters) vs Go 5000 |
| **Memory** | tracemalloc peak reported in bench output; no PDF byte cache |
| **Behavior / correctness** | Deterministic PDFs (fixed created date); compliant vs plain PDF 2.0 modes |
| **API / CLI** | `python3 -m engine.bench_zerodha` / `engine.bench_financial`; Make targets |
| **Dependencies** | Stdlib only for engine + benches |
| **Binary size / build time** | N/A (Python) |

---

## Breaking changes / migration

| Item | Migration |
|------|-----------|
| None | New package under `pythoncoreengine/` |

---

## Test plan

- [x] `python3 -m pytest engine/tests/test_table.py engine/tests/test_layout.py engine/tests/test_bench.py engine/tests/test_financial.py -q` (52 passed)
- [x] `python3 -m engine.bench_zerodha --iterations 1` — writes three warm-up PDFs
- [x] `python3 -m engine.bench_financial --iterations 1` — writes financial_report_output.pdf
- [x] `BENCH_ITERATIONS=20 make bench-zerodha-x2` — multi-run summary
- [x] Visual check: cell text centred in retail/active/HFT PDFs (not on top border)

### Commands

```sh
cd pythoncoreengine
python3 -m pytest engine/tests/ -q
make bench-zerodha
make bench-financial-once
```

---

## Screenshots / sample output

```
zerodha workload: 500 jobs (80/15/5 retail/active/hft) pdfa4+pdfua2 cache=on
  Throughput:        ~9 ops/sec
  cache:             on (model/template only; PDF never cached; seed 42)

financial workload: N jobs (template JSON -> rich tables) pdfa4+pdfua2 cache=on
  cache:             on (template only; PDF never cached)
```

---

## Related issues

- Relates to phase-08 Zerodha / financial benchmark work under `pythoncoreengine/plans/`
- No GitHub issue number linked for this PR

---

## PR metadata checklist (author)

- [x] Self-assigned (`--assignee @me`)
- [x] Labels applied
- [x] Related issues filled (plan refs; no ticket ID)
- [x] Filled body under `PR/pr-pythoncoreengine-pdf-engine.md`

---

## Follow-ups (out of scope)

- Full x10 baselines at production iteration counts (long wall time)
- veraPDF CI gate for every warm-up PDF
- Parallel layout workers (GIL limits serial path)

---

## Reviewer checklist

- [ ] Behavior matches summary and test plan
- [ ] No unrelated changes in diff
- [ ] Public API / CLI changes documented
- [ ] New rules have fixture coverage when applicable
- [ ] PR has assignee and labels
- [ ] Related issues use correct Closes/Relates keywords
- [ ] No secrets or generated artifacts committed
