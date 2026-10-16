---
name: tdd-feature
description: Build any Avodeck feature test-first (red, green, refactor). Use for every new behaviour, endpoint, screen or AI task.
---

# Test-first feature

Never write implementation code before a failing test is committed.

## 1. Understand
- Read the slice brief in `docs/slices/<slice>.md` and the relevant rows of `docs/architecture.md` sections 5 (endpoints) and 6 (AI tasks).
- List the behaviours to build as plain sentences ("Before the page is read, facts return 423").
- Check which root rules (`CLAUDE.md`) apply.

## 2. Red
1. Set the phase: `echo red > .claude/phase`
2. Write tests, outermost first:
   - Playwright acceptance test for the screen behaviour (`frontend/tests/e2e/<slice>/`)
   - API tests for each endpoint (`backend/tests/<slice>/test_<area>_api.py`)
   - Service/domain unit tests (`backend/tests/<slice>/test_<area>_service.py`)
   - Component tests for new UI (`frontend/tests/<slice>/`)
   - For AI tasks: fixture + expected output (see `add-ai-task`)
3. Run them: `make test-slice S=<slice>`.
4. Confirm each fails **for the right reason** (missing route, missing behaviour), not because of a typo or import error. Fix the test if it fails for the wrong reason.
5. Commit only the tests: `git commit -m "test(<slice>): <behaviour>"`.

## 3. Green
1. Set the phase: `echo implement > .claude/phase` (a hook now blocks edits under `tests/`).
2. Write the smallest code that makes the tests pass. Follow `add-endpoint`, `add-screen`, `add-migration`, `add-ai-task` as needed.
3. Run `make test-slice S=<slice>` until green. The post-edit hook runs it for you.
4. If a test seems wrong, **stop** and explain why to the user. Do not edit it.
5. Run `make api-client` if any endpoint changed.
6. Commit: `git commit -m "feat(<slice>): <behaviour>"`.

## 4. Refactor
1. Set the phase: `echo refactor > .claude/phase`
2. Improve names and structure; remove duplication. Tests stay green, untouched.
3. Run `make lint` and `make test`.
4. Commit: `git commit -m "refactor(<slice>): <what>"`.

## 5. Finish
- `echo idle > .claude/phase`
- Tick the behaviour in the slice brief.
- Check the definition of done in the root `CLAUDE.md`.
