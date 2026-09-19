.PHONY: install test lint types check clean demo

install:
	pip install -e ".[dev]"

test:
	pytest -m "not network" --cov=sgcorpus --cov-report=term-missing

lint:
	ruff check src tests

types:
	mypy src/sgcorpus

check: lint test

clean:
	rm -rf data .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage

# A small, polite end-to-end run against the live source.
demo:
	sgcorpus fetch hansard --start 2024-02-05 --end 2024-02-09
	sgcorpus normalise hansard
	sgcorpus index
	sgcorpus stats
	sgcorpus search "mental health" --corpus hansard --limit 5
