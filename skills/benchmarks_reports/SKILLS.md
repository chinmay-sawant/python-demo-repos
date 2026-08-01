# Benchmark reports skill

Produce a **benchmark report** for a scanned or measured target (for example
`pythoncoreengine`). The report is driven by **checklist items**: every
hotspot, measurement, or improvement opportunity is one checklist snippet
that starts with an unchecked box `[ ]` so a human can tick items as they
verify or land fixes.

When an improved version of the code or config is known, include it in the
same snippet under **Improved version**.

---

## When to use this skill

1. Create a working branch for the run.
2. **Capture performance dumps first** (required primary evidence):
   - CPU: `cProfile` → `.pstats` + top call-site text (`cumtime` / `tottime`)
   - Heap: `tracemalloc` snapshot → top traceback + lineno text, peak MB
   - Optional: stack samples (`py-spy`) if available
3. Store dumps under `./benchmarks_reports/<target>/dumps/` (or a path listed in
   run metadata). Name stems by workload (`hft_once`, `financial_once`, …).
4. Optionally re-run harness benches for throughput context — but **checklist
   snippets must cite dump files and dump numbers**, not harness logs alone.
5. Write one markdown report using the template below (usually under
   `./benchmarks_reports/<target>/<timestamp-or-branch>.md`).
6. Fill every checklist snippet from dump metrics + the source line the dump
   points at. Leave `[ ]` unchecked on generation; reviewers mark `[x]` after
   validation.

---

## Run metadata

```yaml
timestamp: YYYY-MM-DDTHH:MM:SSZ
repository: repository-name
repository_path: /absolute/path/to/repository
branch: branch-name
commit: git-revision
bench_target: /absolute/path/to/project
report_path: ./benchmarks_reports/<target>/<report-file>.md
dump_path: ./benchmarks_reports/<target>/dumps/
baseline_path: <project>/baselines/   # optional secondary context only
```

## Dump evidence (primary)

- Build / env: `<python version, CPU count, relevant env vars>`
- Dump capture: `<exact commands or script>`
- CPU dumps: `<stem>.pstats`, `<stem>.cprofile_topsites.txt`, `*.cprofile_cumtime.txt`
- Heap dumps: `<stem>.tracemalloc.txt`, peak/current MB from `<stem>.meta.txt`
- Workload stems: `<what each dump rendered>`

## Optional harness context (secondary)

- Bench commands / baselines may be listed for throughput context.
- **Do not** base checklist claims on harness logs when dumps exist.

## Generation checklist

- [ ] Captured CPU + heap dumps for every planned workload stem.
- [ ] Recorded machine / Python / env so the dump set is reproducible.
- [ ] Linked every snippet to dump paths and concrete dump numbers (cumtime, tottime, ncalls, peak MB, traceback rank).
- [ ] Copied the smallest source excerpt at the dump hot site.
- [ ] Wrote a one- or two-line description and a concrete improvement path from the dump.
- [ ] Added an **Improved version** snippet when a better pattern is known or already in tree.
- [ ] Left every snippet checkbox as `[ ]` until a human verifies the claim.
- [ ] Ran `git diff --check` after writing the report.

---

## Results summary

Prefer dump walls and peaks:

| Stem | Profile wall (cProfile) | Heap-run wall | Peak heap | Primary dump files |
| --- | ---: | ---: | ---: | --- |
| `<stem>` | `<s>` | `<s>` | `<MB>` | `<meta + topsites + tracemalloc>` |

Optional multi-stem comparison of dominant call sites (from topsites dumps).

---

## Checklist snippets

Create **one subsection per checklist item**. Order by dump impact (largest
cumtime / peak heap first). Every subsection **must** start with `[ ]` in the
heading.

### Snippet shape (required)

```markdown
### [ ] `<short-id>` — `<one-line title>`

- Metric (CPU dump): `<cumtime / tottime / ncalls from cProfile>`
- Metric (heap dump): `<peak MB / rank size / count from tracemalloc>` (if relevant)
- Source: `<path:line pointed to by the dump>`
- Checklist theme: `<theme>`
- Related dump: `dumps/<stem>.cprofile_topsites.txt`, `dumps/<stem>.tracemalloc.txt`

**Description:**  
`<1–3 sentences grounded in the dump, not in guesses.>`

**How this can be improved:**  
`<1–3 sentences: concrete next step.>`

Current snippet:

\`\`\`<lang>
<smallest excerpt at the dump hot site>
\`\`\`

Improved version:   <!-- omit if unknown -->

\`\`\`<lang>
<proposed or already-landed improved excerpt>
\`\`\`

**Expected impact:** `<dump-relative or unmeasured>`
```

### Rules for snippets

1. **Checkbox first.** Heading always begins with `[ ]` when generated.
2. **Dump or skip.** No snippet without a dump path and a dump number.
3. **One concern per snippet.** Split PNG decode vs table layout vs encode.
4. **Numbers over adjectives.** Prefer “`decode_png` cumtime 1.043 s” over “slow images”.
5. **Smallest excerpt** at the file:line the dump names.
6. **Improved version optional.** Omit when unknown; mark impact unmeasured.
7. **No false confidence.** If the dump only shows a wait/join cluster, say so.

### Suggested checklist themes (pythoncoreengine)

| Theme | Typical dump evidence |
| --- | --- |
| image decode | `decode_png` / filter loops in cProfile |
| PDF object encode | `encode_value` / `encode_dict` tottime + ncalls |
| dense table layout | `_draw_row` / `wrap_text` cumtime |
| structure allocation | tracemalloc ranks under `begin_cell` / `StructElem` |
| final buffer peak | tracemalloc rank at `bytearray(estimate)` |
| font reload | `generate_subsets` / `from_file` on multi-job dumps |
| compression wait | `_render_page_streams` + thread join cumtime |
| HFT scale | `retail_once` vs `hft_once` meta walls |

---

## Optional: comparison block

| Dimension | A | B | Delta |
| --- | ---: | ---: | ---: |
| Profile wall | | | |
| Peak heap | | | |
| #1 CPU site | | | |
| #1 heap rank | | | |

---

## Final evidence

- Branch / commit: `<branch> @ <sha>`
- Dump directory: `./benchmarks_reports/<target>/dumps/`
- Report file: `<path written by this skill>`
- Validation: `git diff --check` — `<pass/fail>`
- Reviewer notes: `<optional>`
