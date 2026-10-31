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
| draft/processing/active/served → withdrawn | `POST …/withdraw` 🔒 `{reason}` (expert withdrew, discharged, evidence not permitted, conflict) | — | Served reports stay locked; deadlines cancelled; retention clock starts; draft note to instructing parties. The reason is kept encrypted in `case_withdrawals`; withdrawing twice returns 409. Slice C moves the status and publishes `CaseWithdrawn`; slice G cancels the case's open deadlines and C reports how many (`deadlines_cancelled`), and slice H starts the retention clock (`delete_after` needs `case.retention`) |
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
| `split_of_page_id` | null → the page a half was cut from (set once, when the halves are numbered); never back | A |
| `rotation` | any → any (expert); re-runs OCR, keeps verified facts (boxes re-mapped or flagged) | B |
| `unreadable` | false → true (worker); true → false only when an expert transcription is saved | A, B |
| `duplicate_of_page_id` | null → page (worker suggestion or expert); page → null (expert undo) | A, B |
| `relevance` | null → yes/background; any → any by the expert | B |
| `needs_clearer_copy` | false → true → false (expert, `PATCH …/pages/{n}`); true puts the page on the instructing-party request draft (08b) | B |
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
| verified/edited → rejected | Expert | If cited in an opinion: warn, list the citing paragraphs, require confirmation; refused with 409 `served_use_supplementary` when a served or withdrawn report version cites the fact (rule 8) | Citations marked broken; final check fails until fixed |
| rejected → verified | Expert | Page read | Append history |
| verified/edited → same (correct again) | Expert confirms a fact flagged "Please check" (`fact_flags.flagged_for = 'recheck'`) | Only while that flag exists (else 409); the cited words must still show the value (else 422) | `version` rises; recheck flag removed; append `fact_verifications` (correct). Any other check also removes the flag |
| verified → edited, edited → edited (change a checked fact) | Expert | The value must be in the cited words, with the expert's corrected words in place (else 422); during "Please check" new `word_ids` are required (422 `words_needed`); refused with 409 `served_use_supplementary` when a served or withdrawn report version cites the fact (also for a move to the clearer copy and for Not right), and when the expert's corrected words, or a reading they replace, are cited by such a fact (rule 8: use the supplementary report); `facts_guard` 0210 allows verified → edited always | `version` rises (an opinion citing the fact then shows `changed_since_cited`); recheck flag removed if any; append `fact_verifications` (edit, old value kept, `correction_id` of the expert's reading it relies on); a reading that replaces an earlier one flags `recheck` on every other checked fact on any word of either that no longer shows its value |
| (page read again) checked fact → same status | Slice A's job after a turn or a typed-out page | Its cited words read the same in the new words under rule 2's normaliser (`provenance.atoms`) and the value still passes the provenance check | `word_ids` and `bbox` (null for typed words) re-mapped; append `fact_verifications` (`remapped`, no person). Otherwise flagged `recheck`. Every current `word_corrections` row on the page is replaced (`superseded_at`), so a fact that relied on the expert's reading is flagged unless the new words show it (0211) |
| (page) old → clearer copy | Expert moves a fact (`POST …/facts/{id}/move`) | Only to the page that supersedes its own (`superseded_by_page_id`); status kept | `version` rises; append `fact_verifications` (move, old and new page) |
| verified/edited/rejected → suggested | The case's named expert changes (reassignment before serving, S3) | Only from inside `cases_named_expert_reset` (migration 0031; `facts_guard` 0212) | An edited fact gets back the suggestion it had before the latest expert's first change (value, fields, words, box) unless its page was read again or it moved since; `version` rises; recheck flag removed; append `fact_verifications` (`reset`, no person) |
| any → (deleted) | Never | — | Facts are only rejected, never deleted (except case deletion) |

`citable_facts()` = status in (verified, edited) **and** the page has been read.

## Question (`questions.status`) and question set

| From → to | Trigger | Guard |
|---|---|---|
| suggested → confirmed / edited | Expert, screen 06 | Edited questions keep `text_verbatim` |
| question set: open → locked | `confirm-all` | Every question and assumption confirmed or edited; the server refuses with `questions_not_reviewed` while any item is still `suggested` |
| locked → open | Expert "Change the questions": a new `instruction_versions` row (the old lock is never undone) | Not yet served; questions moved to the new version return to suggested; drafts keep their links; warning shown |

Writing (screens 14 and 15) requires a locked question set (409 otherwise). A set locks once (`instruction_versions.locked_at`, `confirm-all`; 409 if already locked or nothing to confirm). A newer letter that adds nothing moves the confirmed questions to the new version and locks it straight away; one that adds a question returns them to suggested with the warning `setup.questions.warning.reopened`. After serving, nothing moves.

## Second reading (`second_readings.status`)

queued → done / refused / failed (the `second_reading` job, T-10, ADR 0030). A new request by the named expert or assistant puts any status back to queued (the old lines are cleared). `refused` means the picture could not be masked safely and nothing was sent; the reason is a code (`low_confidence`, `doubtful_word`, `identifier_unmatched`, `page_changed` and others). `failed` carries `invalid_output` or `unexpected_error`. A job writes only for the request it read. A busy model leaves the row queued and the job pauses.

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

A job paused because the reading service is busy or unreachable (`service_busy`, `model_unavailable`, F5) is resumed by the worker itself (paused → running) after an exponential back-off (doubling from 2 seconds, 2 hours in all); if it is still paused after that, the resumed run ends `failed` (running → failed) with the pause reason as `error_code`, and the retry puts it back on the queue. A resume is not a new attempt. Other pauses (cost guard K3, a cancelled run) wait for a person.

Avodeck support may do the two person-driven moves (`POST /v1/support/jobs/{job_id}/resume` and `/requeue`, permissions.md "Avodeck staff"; `app.support_job_action()`, migration 0035): **support resume** hands a paused job back to the queue and the worker makes the paused → running move when it picks it up (refused while the case is on hold, 409 `case_on_hold`; any status but paused is 409 `job_not_paused`); **support requeue** is failed → queued with `attempts` set back to 0 (any status but failed is 409 `job_not_failed`). What the job was (status, error code, attempts) is kept in `job_support_actions`; the job's own `error_code` stays until the worker picks it up.

Every step is idempotent, so retrying from the start is always safe. Finished steps are recorded in `job_steps` and skipped on retry; a job whose worker stopped sending heartbeats is put back on the queue (F6).

## Upload (`uploads.status`)

```
open ──complete──▶ finalizing ──job verifies all parts──▶ complete
  │                    ├──missing part──▶ open (client resends)
  │                    ├──unreadable/hash/size mismatch──▶ failed
  │                    └──expired past expires_at (job dead)──▶ expired
  ├──client cancels──▶ cancelled
  ├──7 days unfinished──▶ expired
  └──stored file does not match the sha256 sent with `complete`──▶ failed
```

Chunks can be sent (and re-sent) only while `open`. `complete` claims the upload as `finalizing` and enqueues a `finalize_upload` worker job that re-reads, decrypts and hashes every part. Success inserts the document and moves to `complete`; a missing part reopens to `open` so the client can resend; an unreadable part, hash or size mismatch moves to `failed`. Completing twice returns the same document or the same 202. A `failed` upload is closed for good (409 `upload_closed`; F43). Cancelled and failed uploads' parts are cleaned by the daily sweep, which sets `cleaned_at` only after Storage deletion succeeds (R8).

Enforced by `app.uploads_guard` (SQLSTATE `AV409`). The daily `expire_uploads` schedule (`core/housekeeping.py`, keyed by date, F28) expires open uploads past `expires_at` and finalizing uploads whose job is done/failed past `expires_at`, deletes their chunks from Storage (paginated), and returns terminal rows needing cleanup.

## Finding (`findings.decision`)

null → relevant / not_relevant / explained_elsewhere (reason required for the last two). The expert can change a decision at any time; every change is audited. A re-run keeps decisions on findings that still exist.

Status (`findings.status`): open → gone when a re-run of the same check no longer finds it; gone → open if a later run finds it again (its decision is kept). Code never decides or deletes a finding. A `gone` finding the expert has decided stays listed on screen 13, marked "no longer found"; an undecided `gone` finding is hidden. One finding per check and set of evidence facts (`findings_one_each`).

## Comment (`comments.resolved_at`)

open → resolved (any case reader); resolved → open (any case reader). Only the author changes the words; every change is audited. Comments never appear in a report.

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

Rows start `suggested` (worker or a case member with `page.quarantine`). dismissed, undone and purged are terminal; after purging, undo is 409. Any other change is refused by the database. Purging also rejects every fact on the page and blanks its value, fields and box, and once no page of the uploaded document is still in use, the stored file is deleted too (migration 0115).

## Missing item (`missing_items.status`)

| From → to | Trigger | Guard |
|---|---|---|
| open → requested | Request sent (08b) | Linked `instructing_party_requests` row |
| requested → received | Expert links an appended page, or accepts the matcher's suggestion | Page of the case added after the item was listed |
| open/requested → not_available | Expert, reason required | — |
| not_available → open | Expert | Reason cleared |

Items start `open` (the worker, T-07c). `received` is final. `PATCH /cases/{id}/missing-items/{item_id}` (named expert or assistant, `request.draft`) makes only the received, not_available and open changes; `requested` is set only by sending a request (08b). Any other change is 409, and the database refuses it too.

## Fact–question link (`fact_question_links.status`)

suggested → confirmed / rejected (expert); confirmed ↔ rejected (expert, audited). If the fact is later rejected, the link becomes `stale` and the expert is shown it on screen 13. A new instruction version never deletes links; links to removed questions become `stale` (their `question_id` is cleared; the database guard sets `stale`). A `stale` link cannot be confirmed (409 `stale_link`) and never returns to `suggested`. A link whose fact is rejected reads as `stale` on screen 13; its row keeps its status until foundation's `confirmed_links` keeps to checked facts (see `docs/decisions/deferred.md`).

## Point (`points.status`)

suggested → accepted / dismissed (reason) by the expert; either can be changed back. If the underlying fact is rejected or changes version, the point becomes `stale` until reviewed.

## Relevant fact (`relevant_facts.status`)

suggested → dismissed (named expert). Final. A suggestion is no longer shown once the draft cites the fact or the fact stops being citable; it is never suggested again for that question.

## Witness claim (`witness_claims.status`)

suggested → confirmed / edited / dismissed (expert). Becomes `stale` if any fact it relies on changes. A new version of the witness statement adds new claims; old claims stay for the record. Once confirmed, edited or dismissed, a claim changes again only after it becomes `stale`.

## Witness comparison (`witness_comparisons.status`)

queued (named expert asks) → done (claims kept, possibly none) / refused (the model's answer failed twice). Set by the worker only. Final. Comparing the same statement again adds no claims.

## Written question (`written_questions.status`)

received → answering (first edit) → answered (expert marks done) → served (answers document served, then locked). Classification can change only before `answered`. A served answer is changed only by a later round or a supplementary report.

## Written question round

open (received) → answered (every question answered) → served (answers served; `report_servings` row) . open → withdrawn if the party withdraws the questions.

## Opposing claim (`opposing_claims.status`)

unchecked → matches / wrong_page / wrong_value / not_on_page / contradicted (worker) → confirmed by the expert (`expert_confirmed`). The expert can change any result; each change is audited. `read_first` is not a stored status: the API shows it in place of the result (with no quote of ours or reason) until that viewer has read the evidence page, and confirming then is 409 `read_first` (rule 3).

## Session (`sessions`)
`aal1` (password only) → `aal2` (authenticator code entered) → revoked. Since ADR 0025 a session lives at `aal1` until the first risky action: every ordinary route works, and step-up routes, the support console and support grants answer 403 `mfa_required` (`mfa_enrolled` false: set up the authenticator; true: enter a code). Enrolling and verifying from `aal1` move it to `aal2` with `mfa_verified_at` now; `aal2` with the code more than 10 minutes old answers step-up routes 403 `step_up_required`. A recovery code signs in at `aal1` with the authenticator removed. Revoked is final: signed out, signed out everywhere, idle (30 minutes), too old (12 hours) or password reset. Entering a code again keeps `aal2` and restarts the 10-minute step-up window; the cookie changes each time.

## Email change (`email_changes`)
Waiting → one address confirmed → completed (both confirmed). Waiting or half-confirmed → cancelled (a newer request) or expired (after a day). Completed, cancelled and expired are final.

## Membership (`memberships`)

active (`started_at`) → ended (`ended_at`, by leaving, removal or organisation closure); terminal. Role changes are audited updates on an active membership. Guards: an organisation always keeps at least one owner (the last owner cannot leave or be removed); a person's membership of their personal organisation never ends while the account is open. The running app refuses these with 409 `last_owner` ("Make someone else an owner first.", F32) and 409 `personal_organisation` (`memberships_actor`, migration 1001). Closing a team organisation ends every membership, owners included (migration 1005). Only an owner changes an owner's role or makes someone an owner.

## Invite (`invites`)

pending → accepted (by the invited email, before `expires_at`) / cancelled (owner or admin) / expired (7 days). All three outcomes are terminal; a new invite is a new row. A pending invite whose `expires_at` has passed reads as expired and can no longer be accepted (409 `invite_expired`); an accepted or cancelled one answers 409 `invite_closed`. `invites_guard` (migration 1002) allows only these moves.

## Hearing (`hearings.status`)

scheduled → heard / vacated / adjourned. adjourned sets `replaced_by_hearing_id` to a new scheduled hearing. heard, vacated and adjourned are terminal. Changing a hearing marks its trial packs and offline packages `stale`.

## Deadline (`deadlines`)

open → done (`done_at`, answers served or marked done) / cancelled (`cancelled_at`, set when the case is withdrawn, through `CaseWithdrawn`). Cancelled is terminal and never cleared: it cannot be moved or marked done or undone (409 `cancelled`; `deadlines_guard` refuses any change), and a court direction never picks it up. Done and cancelled deadlines get no reminders. A settled, withdrawn, archived or deleted case gets no new pack deadlines (route and `app.after_serving_pack_deadline`, migration 0702).

## Legal hold (`legal_holds`)

active → released (`released_at`, named expert or org owner, step-up) or → released automatically at `until`. While any hold is active the case cannot move to `deleted`.

## Invoice (`invoices.status`)

draft → sent (immutable from here) → part_paid → paid; sent/part_paid → written_off (step-up). part_paid and paid follow the sums: adding a payment (`invoice_payments`) or a credit note makes the invoice part_paid while something is owed and paid when payments reach the total less credit notes. Corrections are `credit_notes`, never edits. paid and written_off are terminal unless a credit note is issued (which is a new row).

## Court bundle version (`court_bundles`)

received → matching (job) → matched → confirmed (expert confirms the concordance) → superseded (a newer version arrives; `superseded_by_id`). Trial packs use the latest `confirmed` version only.

## Report serving (`report_servings`)

A serving row is created once per recipient and never edited. A mistaken serving is corrected by a new row marked `correction_of` with a reason.

## Support access grant

active (named expert grants, step-up, 24 hours) → expired / revoked. Terminal; every view during the grant is in the audit log. A grant counts only while it was given by the case's current named expert; expiry is checked on every read (`app.case_access`). A new grant on the same case revokes the earlier active one, so a case has at most one active grant (slice O's route). Revoking an expired or revoked grant is 409. Grants, revocations, every view under a grant (`support_access.viewed`) and every look from the support console (`support.case_looked_up`) are in the case's audit trail.

## Custody transfer (`custody_transfers`)

active (support records it for a served case, migration 0032) → revoked (`revoked_at`, by support; final). While active, the custodian (the person named, or the current owners and admins of the organisation named) reads the case and nothing more. A case never served cannot be transferred (409), nor can a closed organisation receive one (409 `organisation_closed`). Refused attempts are audited (`support.custody_transfer_refused`).

## Account created by support (`support_accounts`)

invited (support creates it with a temporary password, migrations 1012, 1013; ADR 0024) → active, when the person has chosen their own password (`people.must_change_password` cleared) and completed MFA in a session. Not stored: read from `people` and `sessions`. While invited, support may make a new temporary password (the old one stops working and the person is signed out everywhere; at most once a minute, else 429 `sent_recently`); once active, 409.

## Case replacement (`case_replacements`)

Created once with its case (`app.create_replacement_case()`, migration 0033), only for a case that has been served; never changes. A served case has at most one replacement that is not deleted: asking again for the same expert and consent returns it, for anything else 409 `replacement_exists` (migration 0034). The new case starts as a draft like any other.

## Trial pack and offline package freshness

current → stale when new material (records, report version, hearing change, court bundle version) arrives after `built_at`. Rebuilding creates a new row; stale packs show a warning and can still be opened.

Each pack and copy stores what it was built from (`built_hearing_version`, `built_last_page_no`, `built_served_version_id`, and the court bundle). It reads as stale when it carries a stored mark (`stale`, set when the court bundle is re-issued) or when that snapshot no longer matches the case (`core/contracts/trial_freshness.py`), with `stale_reason` one of `court_bundle_superseded`, `hearing_adjourned`, `hearing_changed`, `new_served_version`, `new_pages`. Only active copies (built or downloaded) are judged. stale → current never happens: a stale copy is deleted and a new one made.
