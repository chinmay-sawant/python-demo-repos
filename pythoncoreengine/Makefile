# pythoncoreengine -- Phase 8 Zerodha-style benchmark targets.
#
# The zerodha bench is a pure-Python workload (sampledata JSON -> model ->
# render -> PDF bytes) honouring the env contract in
# sampledata/zerodha/README.md (BENCH_ITERATIONS / BENCH_WORKERS /
# BENCH_CACHE / BENCH_SEED / BENCH_SKIP_WRITE).  Defaults: 500 iterations,
# compliant (PDF/A-4 + PDF/UA-2), model cache ON.  Use BENCH_ITERATIONS to
# scale, e.g. `BENCH_ITERATIONS=20 make bench-zerodha`.
#
# xN targets run the bench N times in a loop and keep every run's report
# under baselines/zerodha_bench_xN/.

PYTHON ?= python3
BENCH := $(PYTHON) -m engine.bench_zerodha
REPORT_DIR := baselines

.PHONY: bench-zerodha bench-zerodha-nocomply bench-zerodha-cached \
	bench-zerodha-uncached bench-zerodha-x2 bench-zerodha-x5 \
	bench-zerodha-x10 bench-zerodha-nocomply-x10

bench-zerodha:          ## compliant (PDF/A-4 + PDF/UA-2), model cache ON (defaults)
	$(BENCH)

bench-zerodha-nocomply: ## plain PDF 2.0 only
	$(BENCH) --nocomply

bench-zerodha-cached:   ## explicit BENCH_CACHE=1
	BENCH_CACHE=1 $(BENCH)

bench-zerodha-uncached: ## BENCH_CACHE=0: rebuild the model every iteration
	BENCH_CACHE=0 $(BENCH)

bench-zerodha-x2:
	@for i in 1 2; do echo "=== zerodha run $$i / 2 ==="; \
		$(BENCH) --report $(REPORT_DIR)/zerodha_bench_x2/zerodha_py_run$$i.txt; done

bench-zerodha-x5:
	@for i in 1 2 3 4 5; do echo "=== zerodha run $$i / 5 ==="; \
		$(BENCH) --report $(REPORT_DIR)/zerodha_bench_x5/zerodha_py_run$$i.txt; done

bench-zerodha-x10:
	@for i in $$(seq 1 10); do echo "=== zerodha run $$i / 10 ==="; \
		$(BENCH) --report $(REPORT_DIR)/zerodha_bench_x10/zerodha_py_run$$i.txt; done

bench-zerodha-nocomply-x10:
	@for i in $$(seq 1 10); do echo "=== zerodha run $$i / 10 (nocomply) ==="; \
		$(BENCH) --nocomply --report $(REPORT_DIR)/zerodha_bench_x10_nocomply/zerodha_py_run$$i.txt; done
