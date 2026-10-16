# Reviewer checklist

The reviewer session (and you) check every pull request against this list before merging. Copy it into the PR description and tick it.

**Rules**
- [ ] No rule in the root `AGENTS.md` broken (opinion, provenance, blind read, browser → FastAPI only, home region, no patient text in logs, identifiers stripped, served reports locked, access check, step-up, nothing hard-coded)
- [ ] No forbidden word (architecture 3.5) in code, prompts or enums; tests pass under the fixture packs

**Behaviour (the ten questions)**
- [ ] Who can do this? The action is in `docs/spec/permissions.md` and every route calls `require_case_access(action)`
- [ ] How many? If there can be more than one (parties, rounds, versions, recipients), the model allows it
- [ ] What changes over time? History is kept (versions or append-only), not overwritten
- [ ] Every status change is in `docs/spec/state-machines.md` and enforced in the service and a trigger
- [ ] What if two people or two tabs do it at once? ETag/If-Match on edits, Idempotency-Key on creates and sends
- [ ] What if it fails half-way? A failure-mode row and test exist
- [ ] What happens when someone leaves, or the case is withdrawn, held or deleted? Covered in `behaviour-index.md`
- [ ] Is it the named expert's, or the organisation's? Personal things stay personal
- [ ] What does the expert see? Plain words from the glossary, with an i18n key
- [ ] Does it still work in a second country, specialty and case type? (fixture packs)

**Documents**
- [ ] New tables in architecture section 4, `data-dictionary.md` and `behaviour-index.md`
- [ ] New endpoints in architecture section 5 and the slice brief
- [ ] `python scripts/check_spec.py` passes
- [ ] Anything not built but discussed is in `docs/decisions/deferred.md`
