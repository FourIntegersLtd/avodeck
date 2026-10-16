---
name: slice-brief
description: Write or update an Avodeck slice owner brief in docs/slices/ before a slice starts, so one Claude session can own it end to end.
---

# Write a slice brief

Copy this structure into `docs/slices/<letter>-<name>.md`. Pull rows from `docs/architecture.md` sections 4–8.

```
# Slice <X>: <name>

Owner: <Claude session / branch slice/<x>-<name>>   Phase: MVP | 2 | 3   Migration range: <NNNN–NNNN>

## Goal
One or two sentences: what the expert can do when this slice is done.

## Screens
Numbers, names, canvas files.

## Endpoints (from sections 5.2 and 5.4)
Method · path · what it does · 🔒 if step-up.

## Tables owned
Name · key fields. Tables read from other slices (read-only).

## AI tasks (from section 6.3)
Task ID · model · input · output · check.

## Contracts
In: what this slice needs from foundation/other slices (stub until available).
Out: what this slice provides to others.

## Behaviour
Answers to the ten behaviour questions below, one short line each.

## Acceptance tests (write these first; all must fail before work starts)
- [ ] Plain-sentence behaviours, each mapped to a test file.

## Non-negotiable rules that apply
List from root CLAUDE.md.

## Out of scope
What this slice must not touch.

## Definition of done
Root checklist plus anything slice-specific.
```


## Behaviour questions (answer before writing tests)

For every table, endpoint and screen in the brief, answer these in a short "Behaviour" section. Any answer that adds a rule goes into architecture section 10 and becomes an acceptance test.

1. **Who** can do it? (named expert, case member by case role, organisation admin) Add the action to `docs/spec/permissions.md`.
2. **How many** can there be? (parties, versions, rounds, recipients, participants)
3. **What changes over time,** and is history kept?
4. **Which statuses** does it move through? Add them to `docs/spec/state-machines.md`.
5. **Two at once:** what if two people or two tabs act together? (ETag, Idempotency-Key)
6. **Half-way failure:** what if it stops mid-way? Add a row to `docs/spec/failure-modes.md`.
7. **Leaving and ending:** what happens when a person leaves, the case is withdrawn, put on hold, held for litigation, or deleted? Add a line to `docs/spec/behaviour-index.md`.
8. **Personal or organisational?** Does it follow the person or the organisation?
9. **What does the expert see** in plain words, including when it goes wrong?
10. **Second case:** does it still work in another country, case type and specialty (fixture packs), and for a person in two organisations?

Also add each new table to `docs/spec/data-dictionary.md` (every field in plain English) and run `python scripts/check_spec.py`.

Keep it to what one session needs. Update it when scope changes.
