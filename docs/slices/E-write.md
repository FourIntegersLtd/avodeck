# Slice E: Write

Owner: Claude session, branch `slice/e-write` · Phase: MVP · Migrations 0500–0599

## Goal
For each question, the expert sees the facts for and against, notes what other reasonable clinicians might have done, compares witness statements with the records, and then writes or dictates their opinion. They insert verified facts as citations, and gentle warnings flag uncited facts, loaded language or points outside their expertise. The AI sorts and flags; it never writes a word of the opinion.

## Screens
- 14 Points against (`S14-PointsAgainst`): per question; facts by side, unused relevant facts, accounts that differ, range note
- 15 Your opinion (`S15-Opinion`): workspace with fact chips, guidance in force, points summary, dictation, lint warnings, view and provisional flag
- Today cards: registers "questions without an opinion" and "points against not yet considered"

## Endpoints (architecture 5.2)
- `GET /v1/cases/{id}/questions/{q_id}/points`; `POST /v1/cases/{id}/questions/{q_id}/points/suggest` → 202; `PATCH /v1/cases/{id}/points/{point_id}`
- `PUT /v1/cases/{id}/questions/{q_id}/range-note`
- `POST /v1/cases/{id}/witness-statements/{doc_id}/compare` → 202; `PATCH /v1/cases/{id}/witness-claims/{claim_id}`
- `GET /v1/cases/{id}/questions/{q_id}/workspace`
- `PUT /v1/cases/{id}/questions/{q_id}/opinion` (autosave; `body`, `citation_refs`, `view`, `provisional`)
- `POST /v1/cases/{id}/questions/{q_id}/opinion/lint`; `PATCH /v1/cases/{id}/lint-issues/{issue_id}`

## Tables owned
`points` (side: supports_claim / supports_clinician / neutral; labels from the pack), `range_notes`, `witness_claims`, `opinions` (body, citation_refs, view, provisional, test_text), `lint_issues` (keyed by `opinion_id` here; F adds `paragraph_id` rows for the whole report).

## Citation rule (the heart of this slice)
A citation is a structured object `{fact_id, quote_verbatim, page_id}` stored in `citation_refs`, with a marker in `body`. The server accepts it only if:
1. the fact is in `citable_facts(case_id)` (verified, page read);
2. `quote_verbatim`, after normalisation, appears in the fact's cited words.

Otherwise it returns 422. The frontend only inserts chips; it never builds citation text.

## AI tasks (architecture 6.3)
| Task | Model | Input | Output | Check |
|---|---|---|---|---|
| T-14a Sort facts | Sonnet 5 | Question + verified fact IDs with text | `{fact_id, side, reason}` | Output IDs must be a subset of those supplied, else rejected; the expert moves or accepts |
| T-14b Relevant facts not yet used | bge-small → reranker → Sonnet 5 | Draft body + unused verified facts | `{fact_id, why_relevant}` | Shown as suggestions; dismissible |
| T-14c Conflicting accounts | Azure DI → Opus 5.5 | Witness statement text + retrieved verified facts | `claims[{text_verbatim, word_ids}]`, `verdicts[{claim_index, fact_ids, verdict}]` | Claims are exact substrings of the statement; both quotes shown side by side; the expert confirms |
| T-15a Lint | Regex first, then Sonnet 5 | One paragraph + its citation objects + expert specialty | `issues[{sentence_index, type: uncited_fact\|loaded_language\|outside_expertise, words}]` | Warnings only; never rewrites; dismiss with reason |
| T-DICT | Azure Speech | Audio | Transcript into `body` | Expert edits |

The loaded-language word list and opinion `view` options come from the case-type pack.

## Contracts
- **In:** `citable_facts()` and fact flags (B), confirmed questions (C), links and `available_at` (D), guidance in force (D endpoint), expert specialty (C). Stub with `stub_facts.py` (verified) until B lands.
- **Out:** `opinions` with validated citations, points and range notes per question (F builds the report and runs final checks such as "points against considered").

## Acceptance tests (write first)
- [ ] Citing a suggested, rejected or unread-page fact returns 422. Citing a verified fact whose quote is not in its words returns 422. A valid citation is stored and the marker round-trips.
- [ ] T-14a output containing a fact ID that was not supplied is rejected and retried once. The recorded request contains only verified facts with identifiers stripped.
- [ ] Moving a point between sides, accepting and removing are audited. Side labels come from the pack (the gb-eaw/general-practice packs show "supports the GP").
- [ ] Facts flagged on screen 09 appear first under the question they're linked to.
- [ ] T-14b suggests the planted unused fact (safety-netting entry on page 117) after a draft that ignores it.
- [ ] T-14c on the synthetic witness statement finds the planted contradiction ("I was never told to come back" vs page 118 "review 3/52 if no better"). Claims are exact substrings, and the expert's decision is stored.
- [ ] The regex lint flags a sentence with a date or number but no citation, and words from the pack's loaded-language list ("clearly negligent", "obviously"), in under 200 ms.
- [ ] The AI lint flags a causation statement as `outside_expertise` for a general-practice expert (rule from the case-type pack).
- [ ] Lint returns in under 2 s at p95 (recorded responses plus timing budget).
- [ ] No lint response contains replacement text, and the lint schema has no free-prose field (core test).
- [ ] Dismissing a lint issue requires a reason and is kept for the final check.
- [ ] Autosave every 5 s and on blur. Two tabs editing the same opinion: the later save with a stale `updated_at` gets 409 with both versions.
- [ ] The workspace returns verified facts linked to the question as chips, the guidance in force on the consultation date, and the points summary.
- [ ] No slice code contains a clinician title, party or instructing-party word, or a fixed side or view list (core scan); the screens work unchanged with the fixture packs.
- [ ] Screens 14 and 15 pass Playwright + axe and match their canvas boards.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `opinion_versions`.

**Extra endpoints:** `GET /v1/cases/{id}/opinions/{opinion_id}/versions`, `POST /v1/cases/{id}/opinions/{opinion_id}/versions/{version_no}/restore`.

- [ ] Every autosave makes a version; restoring creates a new version, never edits an old one
- [ ] Two windows: the second save gets 412 and the keep-which prompt (S9)
- [ ] Only the named expert can write opinions (403 for co-experts, supervisors and assistants)
- [ ] Points and witness claims become `stale` when an underlying fact changes

## Rules that apply
1, 2, 4, 6, 7, 9, 11 from the root `CLAUDE.md`.

## Out of scope
Report assembly and whole-report lint T-17 (F), changing opinions after serving (G, screen 21b), condition-and-prognosis writing (slice J).

## Definition of done
Root checklist, plus: on staging the expert can write and cite opinions for all four synthetic questions, and every citation passes the server check.
