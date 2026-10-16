# Avodeck developer commands. The API and worker live in api/ (Poetry, Python 3.12); the web app in web/.
# make test-slice S=pipeline  (SLICE=pipeline also works; names are in .claude/routes.json)
SLICE ?=
S ?= $(SLICE)

# Run a command in web/ only once the web app has been set up.
in_web = if [ -f web/package.json ]; then cd web && $(1); else echo "web: not set up yet, skipped"; fi

.PHONY: install dev test test-core test-slice test-api test-web api-client schemathesis eval \
	lint lint-api lint-web eval-data check-spec

install:        ## Poetry env for api/ (and pnpm for web/ once it exists)
	$(MAKE) -C api install
	@$(call in_web,pnpm install --frozen-lockfile)

dev:            ## local Supabase, API, worker and web app
	supabase start
	$(MAKE) -C api start & \
	if [ -f api/app/worker/handlers.py ]; then $(MAKE) -C api worker & fi; \
	if [ -f web/package.json ]; then cd web && pnpm dev; else wait; fi

test: test-api test-web

test-core:      ## the non-negotiable rules' tests
	@$(MAKE) -s -C api guards
	@$(call in_web,pnpm vitest run src/foundation/guards --passWithNoTests)

test-slice:     ## make test-slice S=pipeline
	@test -n "$(S)" || { echo "usage: make test-slice S=<feature>"; exit 2; }
	@$(MAKE) -s -C api test-feature F=$(S)
	@$(call in_web,pnpm vitest run src/capabilities/$(subst _,-,$(S)) --passWithNoTests)

test-api:
	$(MAKE) -C api test

test-web:
	@$(call in_web,pnpm vitest run --coverage)

api-client:     ## regenerate openapi.json and the typed web client
	$(MAKE) -C api openapi
	@$(call in_web,pnpm exec openapi-typescript ../openapi.json -o src/foundation/platform-client/schema.ts)

schemathesis:
	$(MAKE) -C api schemathesis

eval:           ## live models against synthetic bundles
	$(MAKE) -C api eval

lint: lint-api lint-web

lint-api:
	$(MAKE) -C api lint typecheck

lint-web:
	@$(call in_web,pnpm lint && pnpm typecheck)

# Synthetic evaluation data (deterministic; PDFs are not committed)
eval-data:
	python3 evals/generator/build.py evals/gb-eaw/clin-neg-breach/general-practice
	python3 evals/generator/extras.py evals/gb-eaw/clin-neg-breach/general-practice
	python3 evals/generator/inputs.py evals/gb-eaw/clin-neg-breach/general-practice

check-spec:     ## documents agree with each other
	python3 scripts/check_spec.py
