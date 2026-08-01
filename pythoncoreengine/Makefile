# pythoncoreengine -- Phase 8 benchmark targets (Zerodha + financial).
#
# Zerodha: sampledata/zerodha/{retail,active,hft}.json -> ContractNote ->
#          theme tables -> PDF.  Cache is **model/template-only** (PDF never
#          cached).  Warm-up PDFs: zerodha_<tier>[_nocomply]_output.pdf.
# Financial: sampledata/financial/financial_report.json -> PDFTemplate ->
#            rich tables -> PDF.  Same cache contract.
#
# Env (both harnesses):
#   BENCH_ITERATIONS / BENCH_WORKERS / BENCH_CACHE / BENCH_SEED /
#   BENCH_SKIP_WRITE
#
# Defaults: zerodha 500 iters (model cache ON); financial 100 iters
# (template cache ON, compliance from JSON arlingtonCompatible).
#
# Multi-run targets write per-run logs under baselines/*_bench_xN/ and a
# summary at baselines/*_bench_xN_stats_latest.txt:
#
#   # Zerodha/Financial x10 latest summary (pythoncoreengine harness)
#   Runs: 10
#   Best/Worst/Mean/Median throughput ...
#   | Run | Throughput | Avg latency | Peak allocated |

PYTHON ?= python3
BENCH := $(PYTHON) -m engine.bench_zerodha
BENCH_FIN := $(PYTHON) -m engine.bench_financial
REPORT_DIR := baselines
ZERODHA_DIR := sampledata/zerodha
FIN_DIR := sampledata/financial
SUMMARIZE_Z := $(PYTHON) $(ZERODHA_DIR)/summarize_runs.py
SUMMARIZE := $(PYTHON) $(FIN_DIR)/summarize_runs.py

.PHONY: bench-zerodha bench-zerodha-nocomply bench-zerodha-cached \
	bench-zerodha-uncached bench-zerodha-x2 bench-zerodha-x5 \
	bench-zerodha-x10 bench-zerodha-nocomply-x10 \
	bench-financial bench-financial-once bench-financial-nocomply \
	bench-financial-cached bench-financial-uncached \
	bench-financial-x2 bench-financial-x5 bench-financial-x10 \
	bench-financial-nocomply-x10

# ── Zerodha (3 domain JSON fixtures → model cache → PDF every job) ──────────

bench-zerodha:          ## compliant (PDF/A-4 + PDF/UA-2), model cache ON (defaults)
	$(BENCH)

bench-zerodha-nocomply: ## plain PDF 2.0 only
	$(BENCH) --nocomply

bench-zerodha-cached:   ## explicit BENCH_CACHE=1
	BENCH_CACHE=1 $(BENCH)

bench-zerodha-uncached: ## BENCH_CACHE=0: rebuild the model every iteration
	BENCH_CACHE=0 $(BENCH)

bench-zerodha-x2:
	@mkdir -p $(REPORT_DIR)/zerodha_bench_x2
	@for i in 1 2; do echo "=== zerodha run $$i / 2 ==="; \
		BENCH_SKIP_WRITE=1 $(BENCH) --report $(REPORT_DIR)/zerodha_bench_x2/zerodha_run$$i.txt; done
	@$(SUMMARIZE_Z) $(REPORT_DIR)/zerodha_bench_x2 \
		$(REPORT_DIR)/zerodha_bench_x2_stats_latest.txt 0 0 \
		"# Zerodha x2 latest summary (pythoncoreengine harness)"

bench-zerodha-x5:
	@bash $(ZERODHA_DIR)/run_bench_x5.sh

bench-zerodha-x10:
	@bash $(ZERODHA_DIR)/run_bench_x10.sh

bench-zerodha-nocomply-x10:
	@bash $(ZERODHA_DIR)/run_bench_x10_nocomply.sh

# ── Financial report (full-format template → financial_report_output.pdf) ───
# Default `bench-financial` = x10 multi-run + Zerodha-style stats summary.

bench-financial: bench-financial-x10 ## x10 multi-run + summary (default)

bench-financial-once: ## single timed run (template cache ON, JSON compliance)
	$(BENCH_FIN)

bench-financial-nocomply: ## single run, plain PDF 2.0 only
	$(BENCH_FIN) --nocomply

bench-financial-cached:   ## explicit BENCH_CACHE=1 (template reuse)
	BENCH_CACHE=1 $(BENCH_FIN)

bench-financial-uncached: ## BENCH_CACHE=0: re-parse JSON every iteration
	BENCH_CACHE=0 $(BENCH_FIN)

bench-financial-x2:
	@mkdir -p $(REPORT_DIR)/financial_bench_x2
	@for i in 1 2; do echo "=== financial run $$i / 2 ==="; \
		BENCH_SKIP_WRITE=1 $(BENCH_FIN) --report $(REPORT_DIR)/financial_bench_x2/financial_run$$i.txt; done
	@$(SUMMARIZE) $(REPORT_DIR)/financial_bench_x2 \
		$(REPORT_DIR)/financial_bench_x2_stats_latest.txt 0 0 \
		"# Financial x2 latest summary (pythoncoreengine harness)"

bench-financial-x5:
	@bash $(FIN_DIR)/run_bench_x5.sh

bench-financial-x10:
	@bash $(FIN_DIR)/run_bench_x10.sh

bench-financial-nocomply-x10:
	@bash $(FIN_DIR)/run_bench_x10_nocomply.sh
