# Slice I: Trial

Owner: Claude session, branch `slice/i-trial` · Phase: 3 · Migrations 0900–0999

## Goal
The expert prepares the joint statement with the other expert, with deviations from their served report flagged. They map every page they cited to its new number in the trial bundle, build a large-print trial pack, and take an encrypted, read-only offline copy into the witness box, where searching works with no connection.

## Screens
- 22 Joint statement (`S22-JointStatement`): meeting type and wording from the pack
- 23 Trial page numbers (`S23-TrialPages`): pack label "trial bundle"
- 24 Trial pack (`S24-TrialPack`)
- 25 In the witness box (`S25-Lookup`): offline
- Today cards: registers "joint statement to sign by" and "trial pages to confirm"

## Endpoints (architecture 5.2)
- `POST /v1/cases/{id}/expert-meetings`; `POST …/{meeting_id}/agenda` → 202; `GET …/{meeting_id}`; `PATCH /v1/cases/{id}/agenda-items/{item_id}`; `POST …/{meeting_id}/prepare`; `POST …/{meeting_id}/send-for-signature` 🔒 (MVP of this slice: download for signing); `POST /webhooks/esign` (signature-verified)
- `POST /v1/cases/{id}/court-bundles` → 202; `GET /v1/cases/{id}/concordance?status=`; `PATCH /v1/cases/{id}/concordance/{row_id}`; `GET …/concordance/export?format=pdf|csv`; `POST …/concordance/tell-instructing-party`
- `POST /v1/cases/{id}/trial-pack/check-new`; `PUT /v1/cases/{id}/trial-pack/settings`; `POST /v1/cases/{id}/trial-pack` → 202; `GET …/trial-pack/latest`; `GET …/trial-pack/latest/download`
- `POST /v1/cases/{id}/offline-packages` 🔒; `GET …/offline-packages/{pkg_id}/download` 🔒; `DELETE …/offline-packages/{pkg_id}`

## Tables owned
`expert_meetings`, `agenda_items`, `court_bundles`, `concordance`, `trial_packs`, `offline_packages`.

## Worker jobs
- **`match_trial_bundle`:**
  1. Azure DI Read on the trial bundle (thousands of pages).
  2. For each page the expert cited or read, find its trial page: perceptual hash on the image, MinHash on the text, then sequence alignment for runs.
  3. Score each match. Low scores go to the expert. Pages with no match are listed as missing.
- **`build_trial_pack`:** WeasyPrint or Typst PDF at 14–16 pt with the sections chosen:
  - served report with trial page numbers substituted;
  - chronology of verified facts;
  - joint statement;
  - written answers;
  - legal tests from the pack;
  - optional guidance labels.

  The PDF is hashed.
- **Offline package:** a SQLite database with FTS5 containing the served report, answers, joint statement, verified facts (text, old page and trial page) and the concordance. It is encrypted with a key released once at download, stores an expiry date, and is checked for revocation whenever the device is online. It contains no AI, no page images unless the expert opts in, and is read-only.

## AI tasks (architecture 6.3)
| Task | Model | Output | Check |
|---|---|---|---|
| T-22a Agenda items | Azure DI + Sonnet 5 | Same schema as T-06 | Exact substring; expert confirms |
| T-22b Prefill from served report | bge-small + Sonnet 5 | `{agenda_id, paragraph_ids}` | Code inserts the verbatim text; the expert edits |
| T-22c Deviation check | Opus 5.5 | `{status: consistent\|changed\|unclear, reason}` | "Changed position" warning; the expert records the reason (a change of opinion also creates a G `report_changes` row) |
| T-23 OCR | Azure DI Read | Words | — |
| T-24 Nothing new on the day | bge-small + Sonnet 5 | `{sentence_index, source: {document, paragraph_id} \| null}` | Unsourced notes flagged; the expert decides |

## Contracts
- **In:** the served version and citations (F), positions and supplementary versions (G), `citable_facts()` (B), page-read log (B), pack meetings, deadlines and legal tests (foundation).
- **Out:** the trial pack and offline package. H deletes and revokes them on close.

## Acceptance tests (write first)
- [ ] Creating a joint statement meeting sets deadlines from the pack's `meetings.yaml` (`gb-eaw`: sign within 7 days of discussion, send within 14 days of signing) with `rule_ref`.
- [ ] The document includes the pack's standard declarations verbatim.
- [ ] T-22b inserts served paragraphs verbatim by code. The AI returns IDs only (schema test).
- [ ] The planted agenda item where the draft says "probably would have been diagnosed" against the served "more likely than not ... not diagnosed" is flagged `changed`. The expert must record a reason before preparing the document.
- [ ] `send-for-signature` needs step-up. The e-sign webhook rejects a bad signature, and on success stores the certificate and hash and sets `signed`.
- [ ] Synthetic trial bundle (re-ordered, re-paginated, with 3 cited pages removed and 5 re-scanned at lower quality): every cited page is matched, or listed as missing, with the right trial number. The 3 removed pages appear as missing. The re-scanned pages match with a lower score and are queued for confirmation.
- [ ] The concordance exports to PDF and CSV. "Tell the instructing party" (pack label "Tell the solicitor") lists the missing pages with no patient identifiers.
- [ ] The trial pack PDF uses trial page numbers wherever a cited page has a confirmed match, and the old number marked "not in trial bundle" where it doesn't (page 214 in the fixture).
- [ ] Minimum font size in the trial pack is 14 pt (checked by parsing the PDF).
- [ ] T-24 flags a planted note sentence that appears in no served document.
- [ ] Offline package: creation and download need step-up, download works once, and the file is encrypted (no readable text in the bytes).
- [ ] After `expires_at`, the screen 25 shell refuses to open it. After revocation, it refuses at the next online check.
- [ ] Screen 25 search returns "x3/52" from page 118 in under 300 ms with the network disabled (Playwright offline mode).
- [ ] No request leaves the device on screen 25 except the revocation check (network log in Playwright).
- [ ] Screens 22–25 pass Playwright + axe and match their canvas boards.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Test inputs:** synthetic files in `bundles/synthetic-mrs-a/inputs/` (built by `make eval-data`), expected results in `evals/gb-eaw/clin-neg-breach/general-practice/expected/inputs.json` under `agenda_v2`, `court_bundle_v2`.

**Extra tables owned:** `expert_meeting_participants`, `agenda_versions`.

**Extra endpoints:** `GET/POST /v1/cases/{id}/expert-meetings/{meeting_id}/participants`, `GET/POST /v1/cases/{id}/expert-meetings/{meeting_id}/agenda-versions`, `POST /v1/cases/{id}/expert-meetings/{meeting_id}/signed-external`, `POST /v1/cases/{id}/court-bundles/{bundle_id}/supersede`.

- [ ] Meetings take any number of participants, on Avodeck or not
- [ ] A new agenda version shows differences and moves out-for-signature back to draft
- [ ] A document signed outside Avodeck is recorded as `signed_external` with its hash
- [ ] A re-issued court bundle supersedes the old one with a new concordance; trial packs use the latest confirmed version and old packs go stale (S12, F35)
- [ ] Trial packs and offline packages are built per hearing; offline packages carry `format_version` and older apps refuse newer formats

## Rules that apply
1, 2, 4 (screen 25 is the allowed exception), 6, 7, 8, 9, 10, 11 from the root `AGENTS.md`.

## Out of scope
Supplementary reports (G), deletion of packages on close (H calls our revoke), live courtroom features beyond offline search.

## Definition of done
Root checklist, plus: on staging the synthetic case produces a signed joint statement, a confirmed concordance, a trial pack and an offline package that works in airplane mode.
