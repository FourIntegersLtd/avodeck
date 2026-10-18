# State machines

Every status column in the data model changes only along the arrows below. Transitions are enforced in each slice's `service.py`, and **also in the database** with a trigger, so a bug or a direct SQL write cannot skip a step. Any other transition returns 409 `conflict`.

Each table lists: from → to, who or what triggers it, the guard (what must be true), and the side effects.

## Case (`cases.status`)

```
draft ──process──▶ processing ──done──▶ active ──serve──▶ served ──settle──▶ settled ──archive──▶ archived
  │                    │                  │                 │                    │                  │
  │                    └─fail─▶ active*   │                 └─(new material)─────┘                  │
  └──────────────────────────────────── delete (retention due or delete_now) ──────────────────────▶ deleted
archived ──reopen──▶ active (if not past delete_after)
```

| From → to | Trigger | Guard | Side effects |
|---|---|---|---|
| draft → processing | Expert presses "Prepare my bundle" | Conflicts answered; at least one records document uploaded and virus-clean | Enqueue `process_bundle`; audit |
| processing → active | Worker | Job finished (with or without unreadable pages) | "Bundle ready" email; Today item |
| processing → active* | Worker | Job failed after retries | Case opens with a "processing problem" banner and a retry button; no data lost |
| active → served | `POST …/report/versions/{v}/serve` 🔒 | A locked report version exists | `report_served` event; deadlines (G) |
| served → served | New material, questions, supplementary report | — | Stays served; new versions link via `prev_version_id` |
| served/active → settled | Expert (`case.settle`) | — | Retention clock starts: `settled_at` set by the database |
| settled → archived | Expert (`case.archive`) or 30 days after settled (daily close run) | — | Read-only; `settled_at` kept |
| archived → active | Expert "Reopen" (`case.reopen`) | Before `delete_after`; no legal hold needed | Audit; `settled_at` cleared |
| settled → active | Expert "Reopen" (`case.reopen`; settlement fell through, new questions) | Before `delete_after` | Retention clock stops (`settled_at` cleared); restarts from the latest settlement |
| draft/processing/active/served → withdrawn | `POST …/withdraw` 🔒 `{reason}` (expert withdrew, discharged, evidence not permitted, conflict) | — | Served reports stay locked; deadlines cancelled; retention clock starts; draft note to instructing parties. The reason is kept encrypted in `case_withdrawals`; withdrawing twice returns 409. Slice C moves the status; the slices that own deadlines read `withdrawn` (nothing is cancelled by C), and slice H starts the retention clock (`delete_after` needs `case.retention`) |
| withdrawn → archived | Expert or 30 days after | — | Read-only |
| any → deleted | **Blocked** while an active `legal_holds` row exists | — | Deletion job skips the case and notifies the named expert |

**On hold** is a flag, not a status: `on_hold` true/false on draft, active or served cases. While on hold, reminders and deadline alerts pause and processing jobs queue; nothing is deleted. A job of a held case stays `queued` with `error_code` `case_on_hold` (kinds that deal with the hold themselves, the export and the deletion run, still run), and messages about it are kept back (`notifications.held_at`). Cleared by `…/resume`, which releases them (`app.release_case_hold`, migration 0022). Setting or clearing it needs `case.hold` (named expert or assistant; the database checks the column, migration 0019).
| any → deleted | Daily deletion job or `delete_now` 🔒 | `delete_after ≤ today`; `delete_now` (`case.delete_now`) only on a case never served, checked by `app.cases_actor` | `app.purge_case` removes every row (served reports too) and leaves the case row as a tombstone; crypto-shred, certificate, `deletion_ledger`; terminal |

## Page (`pages`)

`pages` has no single status. These flags change independently:

| Flag | Allowed changes | Trigger |
|---|---|---|
| `bundle_page_no` | Set once, never changes (also through quarantine, replacement and rotation) | Worker at split |
| `quarantined` | false → true (expert, or expert accepting a suggestion) → false (undo, only until the image is deleted) | A |
| quarantined image and text | kept → deleted (after a logged request lists the page, or the pack's `quarantine_purge_days` pass after confirming); terminal | A |
| `superseded_by_page_id` | null → page (a clearer copy was uploaded); never back | A |
| `rotation` | any → any (expert); re-runs OCR, keeps verified facts (boxes re-mapped or flagged) | B |
| `unreadable` | false → true (worker); true → false only when an expert transcription is saved | A, B |
| `duplicate_of_page_id` | null → page (worker suggestion or expert); page → null (expert undo) | A, B |
| `relevance` | null → yes/background; any → any by the expert | B |
| read state | unread → read (append-only `page_reads`); never back to unread | B |

## Fact (`facts.status`)

```
suggested ──correct──▶ verified
    │  ╲──edit────────▶ edited ──(counts as verified)
    └──not right──────▶ rejected
verified/edited ──not right──▶ rejected        rejected ──correct──▶ verified
```

| From → to | Trigger | Guard | Side effects |
|---|---|---|---|
| suggested → verified / edited / rejected | Expert on screen 09 | Page has a `page_reads` row (else 423); edited values must pass value-in-span or new word selection | Append `fact_verifications` |
| verified/edited → rejected | Expert | If cited in an opinion: warn, list the citing paragraphs, require confirmation | Citations marked broken; final check fails until fixed |
| rejected → verified | Expert | Page read | Append history |
| any → (deleted) | Never | — | Facts are only rejected, never deleted (except case deletion) |

`citable_facts()` = status in (verified, edited) **and** the page has been read.

## Question (`questions.status`) and question set

| From → to | Trigger | Guard |
|---|---|---|
| suggested → confirmed / edited | Expert, screen 06 | Edited questions keep `text_verbatim` |
| question set: open → locked | `confirm-all` | Every question confirmed or edited |
| locked → open | Expert "Change the questions": a new `instruction_versions` row (the old lock is never undone) | Not yet served; questions moved to the new version return to suggested; drafts keep their links; warning shown |

Writing (screens 14 and 15) requires a locked question set (409 otherwise). A set locks once (`instruction_versions.locked_at`, `confirm-all`; 409 if already locked or nothing to confirm). A newer letter that adds nothing moves the confirmed questions to the new version and locks it straight away; one that adds a question returns them to suggested with the warning `setup.questions.warning.reopened`. After serving, nothing moves.

## Prefill suggestion (`prefill_suggestions.status`)

suggested → accepted / dismissed (named expert or assistant, screen 04; once, 409 after). Also suggested → dismissed by the server when the case's packs change (`PATCH /jurisdiction`) for suggestions holding a pack key (party role, instructing party type, court track), since they were checked against the old packs' words. Enforced by `app.prefill_suggestions_guard`. Accepting records the choice only; the case changes when the expert saves the screen 04 form.

## Report version (`report_versions`)

```
draft ──final check passed + sign 🔒──▶ locked ──serve 🔒──▶ served
draft ──rebuild──▶ (new draft; old draft kept)
served ──correction / change of opinion──▶ new supplementary draft ──sign──▶ locked ──serve──▶ served
```

| From → to | Guard | Notes |
|---|---|---|
| draft → locked | Latest final check passed and is newer than the draft; step-up | SHA-256 of DOCX and PDF stored; row immutable from here (database trigger) |
| locked → served | Step-up | `served_on`, `sent_via` |
| served → withdrawn | Step-up; reason; supplementary statement recorded | Stays locked and readable; final checks and new versions refuse to build on it |
| locked/served → anything else | **Never** (except served → withdrawn above) | Rule 8 |

Enforced by `app.report_versions_guard` and `app.report_versions_actor` (who: → locked needs `report.sign`, → served `report.serve`, → withdrawn `report.withdraw`, `docs/spec/permissions.md`): a new row starts `draft`; draft → locked needs `sha256` and sets `locked_at`; any other change to a locked row raises SQLSTATE `AVLCK`, which the API returns as 409 `locked` (`contracts.reports.ensure_unlocked()` refuses before writing). Fact and question statuses are enforced by `app.facts_guard` and `app.questions_guard` (SQLSTATE `AV409`); `version` rises on every change.

## Job (`jobs.status`)

```
queued ──▶ running ──▶ done
             │  └──▶ failed (after 3 attempts with back-off) ──retry──▶ queued
             └──▶ paused (cost guard K3) ──resume──▶ running
```

Every step is idempotent, so retrying from the start is always safe. Finished steps are recorded in `job_steps` and skipped on retry; a job whose worker stopped sending heartbeats is put back on the queue (F6).

## Upload (`uploads.status`)

```
open ──complete (all chunks in, sha256 matches)──▶ complete
  └──7 days unfinished──▶ expired
```

Chunks can be sent (and re-sent) only while `open`. Completing twice returns the same document. Enforced by `app.uploads_guard` (SQLSTATE `AV409`). The daily `expire_uploads` schedule (`core/housekeeping.py`, keyed by date, F28) expires open uploads past `expires_at`, deletes their chunks from Storage and writes `upload.expired` to the audit log.

## Finding (`findings.decision`)

null → relevant / not_relevant / explained_elsewhere (reason required for the last two). The expert can change a decision at any time; every change is audited. A re-run keeps decisions on findings that still exist.

## Expert meeting (`expert_meetings.status`)

draft → out_for_signature (step-up) → signed (e-sign webhook). draft → signed_external when a document signed outside Avodeck is uploaded (hash stored). out_for_signature → draft if a participant declines or the agenda changes (new agenda version). Signed and signed_external are immutable.

## Offline package

built → downloaded (once) → expired (at `expires_at`) or revoked. Expired and revoked are terminal.

## Deletion

A case in `deleted` is terminal. The certificate row is the only remaining record (no patient data).

## Assumption (`assumptions.status`)

suggested → confirmed / edited (expert, screen 06; `text_verbatim` kept) → suggested only if a new instruction version replaces it. Assumptions belong to the question set: they lock and reopen with it.

## Page quarantine (`page_quarantines.status`)

| From → to | Trigger | Guard |
|---|---|---|
| suggested → confirmed | Named expert (S30) | `fact.verify`; sets `pages.quarantined` and sets the page's suggestions aside |
| suggested → dismissed | Named expert | — |
| confirmed → undone | Named expert | Only before purging; restores the suggestions set aside |
| confirmed → purged | Daily `purge_quarantined_pages` (worker) | A logged request lists the page, or the pack's `quarantine_purge_days` have passed |

Rows start `suggested` (worker or a case member with `page.quarantine`). dismissed, undone and purged are terminal; after purging, undo is 409. Any other change is refused by the database.

## Missing item (`missing_items.status`)

| From → to | Trigger | Guard |
|---|---|---|
| open → requested | Request sent (08b) | Linked `instructing_party_requests` row |
| requested → received | Expert links an appended page, or accepts the matcher's suggestion | Page of the case added after the item was listed |
| open/requested → not_available | Expert, reason required | — |
| not_available → open | Expert | Reason cleared |

Items start `open` (the worker, T-07c). `received` is final. `PATCH /cases/{id}/missing-items/{item_id}` (named expert or assistant, `request.draft`) makes only the received, not_available and open changes; `requested` is set only by sending a request (08b). Any other change is 409, and the database refuses it too.

## Fact–question link (`fact_question_links.status`)

suggested → confirmed / rejected (expert); confirmed ↔ rejected (expert, audited). If the fact is later rejected, the link becomes `stale` and the expert is shown it on screen 13. A new instruction version never deletes links; links to removed questions become `stale`.

## Point (`points.status`)

suggested → accepted / dismissed (reason) by the expert; either can be changed back. If the underlying fact is rejected or changes version, the point becomes `stale` until reviewed.

## Relevant fact (`relevant_facts.status`)

suggested → dismissed (named expert). Final. A suggestion is no longer shown once the draft cites the fact or the fact stops being citable; it is never suggested again for that question.

## Witness claim (`witness_claims.status`)

suggested → confirmed / edited / dismissed (expert). Becomes `stale` if any fact it relies on changes. A new version of the witness statement adds new claims; old claims stay for the record.

## Written question (`written_questions.status`)

received → answering (first edit) → answered (expert marks done) → served (answers document served, then locked). Classification can change only before `answered`. A served answer is changed only by a later round or a supplementary report.

## Written question round

open (received) → answered (every question answered) → served (answers served; `report_servings` row) . open → withdrawn if the party withdraws the questions.

## Opposing claim (`opposing_claims.status`)

unchecked → matches / wrong_page / wrong_value / not_on_page / contradicted (worker) → confirmed by the expert (`expert_confirmed`). The expert can change any result; each change is audited.

## Session (`sessions`)
`aal1` (password only) → `aal2` (authenticator code entered) → revoked. Revoked is final: signed out, signed out everywhere, idle (30 minutes), too old (12 hours) or password reset. Entering a code again keeps `aal2` and restarts the 10-minute step-up window; the cookie changes each time.

## Email change (`email_changes`)
Waiting → one address confirmed → completed (both confirmed). Waiting or half-confirmed → cancelled (a newer request) or expired (after a day). Completed, cancelled and expired are final.

## Membership (`memberships`)

active (`started_at`) → ended (`ended_at`, by leaving, removal or organisation closure); terminal. Role changes are audited updates on an active membership. Guards: an organisation always keeps at least one owner (the last owner cannot leave or be removed); a person's membership of their personal organisation never ends while the account is open.

## Invite (`invites`)

pending → accepted (by the invited email, before `expires_at`) / cancelled (owner or admin) / expired (7 days). All three outcomes are terminal; a new invite is a new row.

## Hearing (`hearings.status`)

scheduled → heard / vacated / adjourned. adjourned sets `replaced_by_hearing_id` to a new scheduled hearing. heard, vacated and adjourned are terminal. Changing a hearing marks its trial packs and offline packages `stale`.

## Legal hold (`legal_holds`)

active → released (`released_at`, named expert or org owner, step-up) or → released automatically at `until`. While any hold is active the case cannot move to `deleted`.

## Invoice (`invoices.status`)

draft → sent (immutable from here) → part_paid → paid; sent/part_paid → written_off. Corrections are `credit_notes`, never edits. paid and written_off are terminal unless a credit note is issued (which is a new row).

## Court bundle version (`court_bundles`)

received → matching (job) → matched → confirmed (expert confirms the concordance) → superseded (a newer version arrives; `superseded_by_id`). Trial packs use the latest `confirmed` version only.

## Report serving (`report_servings`)

A serving row is created once per recipient and never edited. A mistaken serving is corrected by a new row marked `correction_of` with a reason.

## Support access grant

active (named expert grants, step-up, 24 hours) → expired / revoked. Terminal; every view during the grant is in the audit log.

## Trial pack and offline package freshness

current → stale when new material (records, report version, hearing change, court bundle version) arrives after `built_at`. Rebuilding creates a new row; stale packs show a warning and can still be opened.
