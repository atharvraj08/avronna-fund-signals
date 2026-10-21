PY ?= .venv/bin/python
COV = --cov-report=term-missing --cov-fail-under=85

.PHONY: install lint test test-contracts test-sources test-entities test-signals test-scoring test-report test-qa e2e run

install:
	python3 -m venv .venv || true
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

lint:
	$(PY) -m ruff check src tests

test:
	$(PY) -m pytest -q --cov=fundsignals --cov-report=term

test-contracts:
	$(PY) -m pytest -q tests/contracts

test-sources:
	$(PY) -m pytest -q tests/sources --cov=fundsignals.sources $(COV)

test-entities:
	$(PY) -m pytest -q tests/entities --cov=fundsignals.entities $(COV)

test-signals:
	$(PY) -m pytest -q tests/signals --cov=fundsignals.signals $(COV)

test-scoring:
	$(PY) -m pytest -q tests/scoring --cov=fundsignals.scoring $(COV)

test-report:
	$(PY) -m pytest -q tests/report --cov=fundsignals.report $(COV)

test-qa: e2e

e2e:
	$(PY) -m pytest -q tests/e2e

run:
	$(PY) -m fundsignals.cli run --demo
