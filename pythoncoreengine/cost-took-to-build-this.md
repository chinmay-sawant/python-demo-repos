# Cost to Build This (deepseek-v4-flash)

**Timeframe:** ~3–4 hours — from scratch (empty repo, no code) to a compliant
PDF engine: PDF 2.0 core writer → layout → TTF subsetting/embedding → PDF/A-4 →
PDF/UA-2 dual compliance (veraPDF `-f 4` + `-f ua2`, 0 failed rules) → perf
pooling → all Phase-7 features (outlines, forms, signatures, encryption, SVG) →
Zerodha bench.

**Usage window:** Jul 31 2026 23:00 → Aug 1 2026 15:05 (first call 23:09:21,
last call 15:04:59). All 1,984 paid calls landed on Aug 1.

## Token & cost summary

| Metric | Tokens | Cost |
|---|---|---|
| Input (cache miss) | 2,109,409 | — |
| Cache read | 261,184,896 | — |
| Output | 775,664 | — |
| Reasoning | 961,375 | — |
| **Total consumed** | **265,031,344** (~265M) | **$1.5130** |
| Assistant messages | 1,984 | — |

## Breakdown by model (same window, for context)

| Model | Messages | Total tokens | Cost |
|---|---|---:|---:|
| deepseek-v4-flash | 1,984 | 265,031,344 | $1.5130 |
| deepseek-v4-flash-free | 470 | 34,767,810 | $0.00 |
| unknown (0-token rows) | 67 | 0 | $0.00 |

## Per-day

| Day | Msgs | Input | Cache read | Output | Reasoning | Cost $ |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-01 | 1,984 | 2,109,409 | 261,184,896 | 775,664 | 961,375 | 1.5130 |

## Top cost sessions

| Session | Msgs | Input | Cache read | Output | Reasoning | Cost $ |
|---|---:|---:|---:|---:|---:|---:|
| Phase 5: PDF/UA-2 tagging | 256 | 181,741 | 56,621,056 | 98,435 | 119,099 | 0.2449 |
| Phase 3: TTF subset + embed | 171 | 100,564 | 27,317,760 | 78,871 | 78,685 | 0.1347 |
| Perf audit & improvements | 191 | 132,266 | 29,945,088 | 48,944 | 60,977 | 0.1331 |
| Phase 7.5 encryption (parallel) | 123 | 113,500 | 20,492,928 | 67,625 | 97,188 | 0.1194 |
| Phase 7.1–7.3 outlines/links/forms | 147 | 122,482 | 22,117,632 | 52,996 | 46,475 | 0.1069 |
| Phase 6: perf & pooling | 146 | 110,389 | 21,048,448 | 60,242 | 40,924 | 0.1027 |
| Phase 7.4: signatures | 145 | 100,609 | 19,309,824 | 72,332 | 34,647 | 0.0981 |
| Phase-wise implementation (main) | 159 | 91,650 | 17,377,792 | 71,057 | 22,024 | 0.0876 |
| Phase 4: PDF/A-4 | 110 | 108,137 | 13,875,200 | 47,093 | 42,894 | 0.0792 |
| Phase 2: layout primitives | 96 | 55,737 | 10,003,456 | 48,587 | 51,269 | 0.0638 |
| Phase 8: Zerodha bench | 52 | 107,614 | 5,887,232 | 24,332 | 45,027 | 0.0510 |
| Phase 7.5 encryption (stuck retries) | 13 | 122,794 | 832,640 | 1,391 | 104,286 | 0.0491 |
| Phase 7.6 SVG (2 failed agents) | 24 | 177,596 | 1,120,640 | 2,616 | 64,589 | 0.0469 |
| Phase 1: core writer | 44 | 35,732 | 2,451,968 | 20,538 | 27,962 | 0.0254 |
| Audit/fix sessions (django/flask/fastapi) | ~277 | ~374,000 | ~10,300,000 | ~94,000 | ~90,000 | ~0.1460 |
| **TOTAL** | **1,984** | **2,109,409** | **261,184,896** | **775,664** | **961,375** | **$1.5130** |

## Notes

- ~98.5% of billed tokens were cache reads (context-heavy subagent sessions),
  which is what keeps the bill at $1.51 for ~265M tokens.
- The two stuck 7.5/7.6 subagent retries cost ~$0.096 combined.
- Cost is the amount opencode recorded per message (`cost` field) in its local
  session DB.
- Source: `~/.local/share/opencode/opencode.db` (`message` table, JSON `data`
  field: `modelID`, `tokens`, `cost`).
- Everything built with pure Python stdlib — no third-party runtime libraries.
- Final state: 566 unit tests green, all compliant fixtures pass veraPDF
  `-f 4` + `-f ua2` with 0 failed rules.
