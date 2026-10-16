# Avodeck

Records-review engine for expert witnesses (launch: England and Wales, clinical negligence breach of duty, general practice). Starter repository: rules, Claude Code setup, CI and slice briefs. No application code yet; the foundation slice builds it test-first.

## Start here
0. Foundation stage 1 runs locally and can start now. Run spikes 1–4 and 8 (`docs/spikes/README.md`) before stage 2, the cloud parts.
1. Read `CLAUDE.md` (rules and how we work).
2. Read `docs/architecture.md` sections 1–3 (rules, system shape, engine and packs), then the sections your slice brief points to.
3. Open `docs/slices/0-foundation.md` and run the `tdd-feature` skill in Claude Code.

## Running slices in parallel
```
git worktree add ../avodeck-a -b slice/a-pipeline
git worktree add ../avodeck-c -b slice/c-setup
git worktree add ../avodeck-h -b slice/h-close
```
Open a Claude Code session in each worktree and give it its brief from `docs/slices/`.

## What's here
| Path | What |
|---|---|
| `CLAUDE.md`, `backend/CLAUDE.md`, `worker/CLAUDE.md`, `frontend/CLAUDE.md` | Rules and conventions Claude Code reads |
| `.claude/skills/` | `tdd-feature`, `add-endpoint`, `add-ai-task`, `add-screen`, `add-migration`, `slice-brief`, `verify-slice` |
| `.claude/agents/`, `.claude/OWNERS.md`, `.claude/routes.json` | One owner agent per slice, and the paths and keywords that route prompts to it |
| `.claude/settings.json`, `.claude/hooks/` | Freeze tests during implementation; run slice tests after each edit; suggest the slice owner for each prompt (hint only) |
| `.github/workflows/` | CI (tests, red-then-green check, Schemathesis, host check, Playwright + axe) and nightly AI evaluation |
| `scripts/` | Red-then-green checker, frontend host check, `check_spec.py` (documents agree with each other) |
| `docs/decisions/` | Decision records 0001–0013 and `deferred.md` |
| `packs/` | Four pack layers: `jurisdiction/gb-eaw`, `case-type/clin-neg-breach`, `specialty/general-practice`, `overlay/gb-eaw/general-practice`; plus fixture packs in `_fixtures/` that prove nothing is hard-coded |
| `docs/research/` | Background research, costs, vendor and market notes (not needed for building) |
| `docs/spec/` | Glossary, design canvas index, NFRs, state machines, failure modes, permissions, scenarios, data dictionary, behaviour index, reviewer checklist |
| `docs/security/`, `docs/compliance/`, `docs/spikes/` | Threat model, intended purpose, pre-build experiments (spikes 1–4 and 8 before foundation stage 2) |
| `evals/` | The synthetic Mrs A case (900 pages), behaviour-test inputs and expected answers; `make eval-data` builds the PDFs |
| `docs/slices/` | Briefs for foundation, A–I and O |
| `docs/design/` | Copy of all 33 screens and the design system: pictures and HTML mock-ups |
| `Makefile` | Developer commands (filled in by the foundation slice) |
