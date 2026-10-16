# 0012. Accounts, organisations and behaviour rules

Status: accepted (items marked **legal check** pending a lawyer's confirmation) · Date: 2026-10-16

## Context
The data model had organisations and memberships but no rules for how they behave: who sees what, what happens when someone leaves, how a solo expert who also works for an agency signs in. A behaviour audit across every area (people, cases, records, facts, writing, reports, court, money, system, AI, packs, data protection, usability) found similar gaps elsewhere: no history of instructions, no handling of wrong-patient pages, no versions of court bundles, no legal hold, nothing to stop a restored backup bringing a deleted case back.

## Decision
1. **Person ≠ expert ≠ organisation.** `people` is identity (one sign-in per cell); `experts` is an optional professional profile; `organisations` own cases and pay. Every person has a personal organisation; teams are more organisations; `memberships` link them with a role.
2. **One named expert per case**, fixed after serving. Reading, verifying, writing, signing and serving are personal to them. Others help through `case_members` with case roles.
3. **One access check**, `require_case_access(action)`, driven by `docs/spec/permissions.md`. Organisation admins get metadata, never content, unless added to the case.
4. **Unified dashboard** across all of a person's organisations.
5. **Named expert keeps access to named cases after leaving an organisation.** **(legal check)** The court duty is personal; the expert may be called years later.
6. **Account closure keeps served cases** read-only until retention; sign-in stays for them. **(legal check)**
7. **Records custodian** can be given read-only custody on death or incapacity. **(legal check)**
8. **Controller/processor:** the expert is controller for personal cases, the organisation for team cases, Avodeck the processor. **(legal check)**
9. **Behaviour rules** in architecture section 10 are binding; every rule has a test, every table has a behaviour line, a plain-English data-dictionary entry and, if it has a status, a state machine. `scripts/check_spec.py` enforces the mechanical parts in CI.
10. **History over overwrite:** instructions, opinions, deadlines, court bundles, agendas and servings are versioned or appended, never edited in place.

## Consequences
- Foundation builds people, personal organisations, memberships, case members and the access check now, so slice O (teams, Phase 2) adds behaviour, not schema rewrites.
- More tables (85) and endpoints, but each is small and has a clear owner.
- Legal checks must be closed before slice O ships and before any team customer.
- New features follow the behaviour questions in the `slice-brief` and `add-migration` skills, and the reviewer checklist.
