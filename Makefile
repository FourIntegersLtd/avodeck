# Avodeck developer commands. Targets are stubs until the foundation slice fills them in.
S ?= core

dev:            ## API, worker, frontend and local Supabase
	supabase start
	cd backend && uvicorn app.main:app --reload --port 8000 & \
	cd backend && python -m app.worker & \
	cd frontend && pnpm dev

test: test-backend test-frontend

test-core:
	cd backend && python -m pytest -q tests/core
	cd frontend && pnpm vitest run tests/core

test-slice:     ## make test-slice S=a
	cd backend && python -m pytest -q tests/$(S)
	cd frontend && pnpm vitest run tests/$(S) --passWithNoTests

test-backend:
	cd backend && python -m pytest -q --cov=app --cov-fail-under=85

test-frontend:
	cd frontend && pnpm vitest run --coverage

api-client:     ## regenerate openapi.json and the typed frontend client
	cd backend && python -m app.export_openapi > ../openapi.json
	cd frontend && pnpm exec openapi-typescript ../openapi.json -o src/api/schema.ts

schemathesis:
	schemathesis run openapi.json --base-url http://localhost:8000 --checks all

eval:           ## live models against synthetic bundles
	cd backend && python -m app.evals.run --threshold 0.95

lint: lint-backend
	cd frontend && pnpm lint && pnpm typecheck

lint-backend:
	cd backend && ruff check . && mypy app

# Synthetic evaluation data (deterministic; PDFs are not committed)
eval-data:
	python3 evals/generator/build.py evals/gb-eaw/clin-neg-breach/general-practice
	python3 evals/generator/extras.py evals/gb-eaw/clin-neg-breach/general-practice
	python3 evals/generator/inputs.py evals/gb-eaw/clin-neg-breach/general-practice

check-spec:     ## documents agree with each other
	python3 scripts/check_spec.py
