# Makefile for Dexalot Python SDK
# Run from the repository root. Uses local .venv managed by uv.
# Setup: uv venv && uv sync --group dev

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
MYPY_CONFIG := --config-file mypy.ini

.PHONY: setup test cov cov-file int int-file lint lint-fix format mypy typecheck clean docs-serve docs-build

setup:
	uv venv && uv sync --group dev

test:
	PYTHONPATH=.:./src $(PYTEST) tests/unit

cov:
	PYTHONPATH=.:./src $(PYTEST) --cov=src --cov-report=term-missing tests/unit

int:
	PYTHONPATH=.:./src $(PYTEST) tests/integration -v -s

int-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Error: FILE argument is required. Usage: make int-file FILE=tests/integration/test_01_readonly.py"; \
		exit 1; \
	fi
	PYTHONPATH=.:./src $(PYTEST) $(FILE) -v -s

# Usage: make cov-file FILE=tests/unit/core/test_clob.py
cov-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Error: FILE argument is required. Usage: make cov-file FILE=tests/unit/core/test_clob.py"; \
		exit 1; \
	fi
	PYTHONPATH=.:./src $(PYTEST) --cov=src --cov-report=term-missing $(FILE)

lint:
	$(RUFF) check .

lint-fix:
	$(RUFF) check . --fix

format:
	$(RUFF) format .

mypy:
	$(MYPY) $(MYPY_CONFIG) --follow-imports=silent

typecheck: mypy

docs-serve:
	uv run --group docs zensical serve

docs-build:
	uv run --group docs zensical build

clean:
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
