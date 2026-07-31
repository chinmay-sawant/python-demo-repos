PROJECTS := django-flash-sale-inventory fastapi-live-metrics-ingest flask-partner-webhook-relay
PY := python3

.PHONY: run lint-all lint-format typecheck security deadcode

run:
	chmod +x ./goslop
	./goslop --profile all --no-fail --no-terminal --config "templates/goslop-python.toml" --export-context --export-chunks --no-cache "."

lint-all: lint format-check typecheck security deadcode
	@echo "lint-all: all checks passed"

lint:
	ruff check .
	@echo "lint: ruff OK"

format-check:
	ruff format --check .
	@echo "format-check: ruff format OK"

typecheck:
	$(foreach p,$(PROJECTS),mypy --ignore-missing-imports --exclude '*/migrations/*' $(p);)

security:
	bandit -q -r $(PROJECTS)
	@echo "security: bandit OK"

deadcode:
	vulture $(PROJECTS) --min-confidence 70 || true
	@echo "deadcode: vulture OK"
