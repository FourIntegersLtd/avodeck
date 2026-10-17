# Avodeck API and worker (one Poetry project, Python 3.12). Run from api/ or via the root Makefile.
.PHONY: help install start worker test test-unit guards test-feature lint typecheck packs openapi schemathesis eval ci

# pytest on the given paths; no tests collected (exit 5) passes until those tests exist.
# TODO: once the first rule test lands in tests/guards, make `guards` fail on exit 5.
pytest_or_none = poetry run pytest -q $(1); rc=$$?; if [ $$rc -eq 5 ]; then echo "$(1): no tests yet"; rc=0; fi; exit $$rc

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/'

install:        ## Poetry env on Python 3.12
	poetry env use -q python3.12 && poetry install -q

start:          ## API on :8000 with reload
	poetry run uvicorn app.main:app --reload --port 8000

worker:         ## background worker (Postgres job queue)
	poetry run python -m app.worker.main

test:           ## every API and worker test, with the coverage floor
	poetry run pytest -q --cov=app --cov-fail-under=85

test-unit:
	@$(call pytest_or_none,tests/unit)

guards:         ## the non-negotiable rules' tests (tests/guards)
	@$(call pytest_or_none,tests/guards)

test-feature:   ## make test-feature F=pipeline
	@test -n "$(F)" || { echo "usage: make test-feature F=<feature>"; exit 2; }
	@files=$$(ls tests/*/test_$(F)_*.py 2>/dev/null); \
	if [ -z "$$files" ]; then echo "$(F): no tests yet"; else poetry run pytest -q $$files; fi

lint:
	poetry run ruff check .

typecheck:
	poetry run mypy app tests

packs:          ## schemas, lint and every combination of the launch and fixture packs
	poetry run python -m app.core.packs ../packs ../packs/_fixtures

openapi:        ## write ../openapi.json and the pack data the web tests use
	poetry run python -m app.export_openapi > ../openapi.json
	poetry run python -m app.export_openapi --pack-fixtures > ../web/src/foundation/platform-client/pack-fixtures.json

# The API must be running (CI starts it with scripts/local_api_env.sh). Settings: schemathesis.toml.
API_URL ?= http://localhost:8000
schemathesis:
	poetry run schemathesis run ../openapi.json --url $(API_URL)

eval:           ## live models against synthetic bundles
	poetry run python -m app.evals.run --threshold 0.95

ci: lint typecheck packs guards test
