# Behaviour index

One line per table, answering the behaviour questions in plain words: who owns it, who can change it, how many there can be, how it changes over time, and what happens when a person leaves or the case is deleted. `scripts/check_spec.py` fails if a table in `docs/architecture.md` section 4 has no line here. Detail lives in section 10, `permissions.md` and `state-machines.md`.

"Named" = the case's named expert. "Org admin" = owner or admin of the organisation that owns the case. "Case deletion" = removed with the case and its key destroyed (crypto-shredded).

| Table | Owner | Who can change it | How many | Over time | Leaving / deletion |
|---|---|---|---|---|---|
| `people` | Foundation | The person | One per person per cell | Email change needs both addresses; closure keeps sign-in for served cases | Closed account: kept while any served case they're named on exists |
| `experts` | Foundation (C fills) | The person | 0 or 1 per person | Edits never touch signed reports (snapshots) | Kept with the person |
| `expert_roles` | C | The expert | Many | Edit freely; snapshot at signing | With the person |
| `expert_training` | C | The expert | Many | Edit freely; snapshot at signing | With the person |
| `report_templates` | C | Person, or org admin for org templates | Many per owner; `version` rises | Replacing makes a new version; reports keep theirs | Org template stays with the org |
| `declarations` | C | The expert | One per jurisdiction per kind | Locked after first served report; later edits are new rows | With the person |
| `signatures` | C | The expert | Many; latest used | New image is a new row; snapshot at signing | With the person |
| `sessions` | Foundation | The person (revoke) | One per device | Expire on inactivity | All revoked on password reset or closure |
| `mfa_recovery_codes` | Foundation | The person (regenerate) | 10 at a time | Each used once; regenerate replaces all | Deleted on closure |
| `mfa_attempts` | Foundation | System (verify and recovery routes) | One per person | Failures counted in a 15-minute window; reset on success; last accepted code kept 90 seconds | Deleted on closure |
| `email_changes` | Foundation | The person (step-up) | One pending per person; a new request cancels the old | Waiting → completed when both addresses confirm; links expire after a day | Deleted on closure |
| `organisations` | Foundation (O for teams) | Owner/admin | Personal: one per person; teams: any | Closure only when no open cases | Served cases outlive closure until retention |
| `memberships` | Foundation (O) | Owner/admin; the person can leave | Many per person; one active per person per org | active → ended; never deleted | Ending removes org access except named cases |
| `invites` | O | Owner/admin (step-up); only an owner invites an owner; accepted by the invited email only | Many | pending → accepted/cancelled/expired; used once | Expire after 7 days; pending ones cancelled when the organisation closes |
| `billing_profiles` | O (personal ones in F) | Person or org owner | One per issuer | Number sequence never skips; edits don't touch sent invoices | Kept with sent invoices for tax |
| `notification_preferences` | Foundation | The person | One per person per org | Edit freely | Deleted with the membership |
| `support_access_grants` | O (table created by foundation, migration 0030) | Named expert grants or revokes; only `revoked_at` changes | Any; one active per case (O's route) | Expire after 24 hours, checked on every read; a revocation is final | Kept after case deletion (record of consent, ids and a code only); opens nothing on a deleted case |
| `custody_transfers` | O (table and access rule created by foundation, migration 0032) | Support staff record (their own name, served cases only, flag `support_custody_transfer`); only `revoked_at` changes | Any; each opens the case read-only to its custodian | active → revoked (final) | Kept after case deletion (who holds the records; ids, dates and a short note about the expert) |
| `case_replacements` | Foundation (O's replacement route calls it) | Organisation owner/admin through `app.create_replacement_case()` only | One per replacement case; a served case has one not deleted (asking again returns it, migration 0034) | Never changes | Kept after deletion of either case (record of consent) |
| `support_staff` | Foundation | Operators by hand in SQL (`docs/ops/support-staff.md`); never the app | One per person | Added; removed by setting `removed_at` (never deleted) | A removed person's grants stop working at once |
| `cases` | Foundation (C fills) | Named expert; org admin for admin fields | Many per org | State machine; on hold flag; named expert fixed after serving; settled_at and closed_at set by the system | Case deletion; legal hold blocks |
| `case_members` | C | Named expert or org admin | Many per case | Added and removed with dates, history kept | Removed when membership ends unless named |
| `case_parties` | C | Named expert or assistant | Many | Editable until served; then additions only (edit or removal refused, 409) | Case deletion |
| `case_instructing_parties` | C | Named expert or assistant | One or more (joint) | Change of firm = new row with dates | Case deletion |
| `case_links` | C | Named expert | Many | Add or remove; needs access to both cases (the other answers not found); never shares data | Removed when either case is deleted |
| `instruction_versions` | Foundation table (0019), C fills | Named expert or assistant | Many, ordered | Append only | Case deletion |
| `case_instructions` | C | Named expert or assistant | One per oral instruction version | Words replaced while the version is unlocked; never deleted | Case deletion |
| `case_subject_keys` | C | Named expert or assistant | One per case | Replaced when the identifier changes; keyed hash only | Case deletion |
| `conflict_answers` | C | Named expert | One per question | Changed in place (version rises); snapshot at signing | Case deletion |
| `conflict_answer_history` | C | Database trigger on `conflict_answers` (no direct writes) | One per answer given or changed | Append only; same answer again adds nothing | Case deletion |
| `conflict_declarations` | C | Named expert | Many; latest is current | Append only | Case deletion |
| `case_withdrawals` | C | Named expert (step-up) | One per case | Append only | Case deletion |
| `questions` | C | Named expert | Many per instruction version | Locked with the set (answer parts and expertise source too); reopened by new instructions before serving | Case deletion |
| `assumptions` | C | Named expert | Many | Lock and reopen with the question set | Case deletion |
| `instruction_extractions` | C | Worker (`extract_questions`) | One per letter version | Append only; counts only | Case deletion |
| `prefill_suggestions` | C | Worker (T-04); named expert or assistant decide | Many per letter version | Decided once (accepted/dismissed); never applied by the server | Case deletion |
| `prefill_runs` | C | Worker (`prefill_case`) | One per run | Append only; counts only | Case deletion |
| `documents` | Foundation (A) | Named expert or assistant (upload) | Many; bundle versions | Never edited after hashing; provenance editable | Case deletion |
| `document_scans` | A | Worker | One per document | Scan result set once; split time set once | Case deletion |
| `document_parts` | A | Worker | Many per email or zip | Insert only | Case deletion |
| `exhibits` | A | Named expert or assistant | Many | Listed only | Case deletion |
| `pages` | Foundation (A, B) | Worker; named expert for flags | Many | Number permanent; quarantine, replacement, rotation (also set once by auto-straighten) per state machine | Case deletion; quarantined images deleted earlier |
| `page_analysis` | A | Worker; named expert or assistant for duplicate review | One per page | Worker notes; duplicate review with If-Match; cleared when a quarantined page is deleted | Case deletion |
| `page_embeddings` | A | Worker | One per page | Remade only when the embedder changes; made from stripped text | Case deletion |
| `page_quarantines` | A | Worker or case member suggests; only named expert confirms or undoes | Many per page; one open | State machine in the database; image and text deleted once told or after the pack's days | Case deletion |
| `documents.split_of_page_id`, `pages.split_of_page_id` (columns, ADR 0019) | A | Named expert or assistant (page.replace) | One split per page | Set once when the halves are numbered; the original keeps its number, facts and reads | Case deletion |
| `page_replacements` | A | Named expert or assistant (page.replace) | One per page; one per copy | Link set once; nothing moves by itself | Case deletion |
| `ocr_words` | A | Worker; re-run on rotation | Many per page | Replaced only by rotation or opt-in reprocessing | Case deletion |
| `facts` | Foundation (A writes, B verifies) | Worker suggests; only named expert verifies | Many per page | Never deleted; `version` rises on change; state machine in the database; citable only when verified or edited on a page the named expert has read | Case deletion; `date_basis` is `printed`, `printed_century`, `line_above` or `please_check`; the expert's verify or correct overrides it (ADR 0017) |
| `fact_verifications` | B | Named expert (append only); the reading job adds `remapped` rows with no person (0207) | Many per fact | Append only | Case deletion; chain head on certificate |
| `page_reads` | B | Anyone who reads; only named expert's count for citing | Many per page per person | Append only, hash-chained | Case deletion; chain head on certificate |
| `fact_flags` | B | Named expert (`points`); the reading job adds `recheck` with no person (0205), removed when the expert checks the fact again | Many | Add or remove | Case deletion |
| `word_corrections` | B | Named expert only (`fact.verify`), while changing a fact | Many per page; one current per word | Never deleted by the app; a later reading of the same words, or new words for the page (slice A's reading job, 0211), sets `superseded_at`; other checked facts on the words of either reading that it no longer shows are flagged `recheck`; refused (409) on words a served fact cites | Goes with its page on case deletion (no case_id, decision 0018); deleted by `purge_quarantined_page` (0211) |
| `page_turns` | B | Named expert or assistant (`page.rotate`), turning a page on screen 09 | One per page | Replaced by the next turn; never deleted by the app; display only (no reading again, words and facts unchanged) | Goes with its page on case deletion (no case_id, like `word_corrections`) |
| `page_notes` | B | Each person for themselves (`page.read`), bookmarking or noting a page on screen 09 | One per person and page | Replaced by the next save; removed when bookmark and note are both cleared; only its owner sees it | Goes with its page on case deletion (no case_id, like `page_turns`) |
| `missing_items` | A | Worker suggests; named expert or assistant updates | Many | State machine | Case deletion |
| `record_gaps` | A | Worker; named expert confirms | Many | Recalculated when records are appended | Case deletion |
| `instructing_party_requests` | A | Named expert or assistant drafts; named expert sends | Many | Sent ones never edited (append only, hash-chained) | Case deletion |
| `case_processing` | A | Named expert or assistant (case.upload) | One per case | Cancel and reprocess opt-in set and cleared | Case deletion |
| `fact_question_links` | D | Named expert | Many | State machine; stale when fact or question changes | Case deletion |
| `check_config` | D | The expert | One per check per expert, including checks they add | Edit freely | With the person |
| `findings` | D | Worker; named expert decides | Many | Decisions kept across re-runs; gone, never deleted; decided gone stays listed | Case deletion |
| `check_runs` | D | Worker | One per run | Append only | Case deletion |
| `case_guideline_versions` | D | API when first shown; worker on re-run | One per guideline version per case | Fingerprint kept; a change shows a notice until checks run again | Case deletion |
| `availability_overrides` | D | Named expert | One per fact | Latest wins; history audited | Case deletion |
| `consultation_notes` | D | Each case member allowed to add notes | One per consultation per person | Private to its author; edit freely | Case deletion |
| `guideline_versions` | D | Pack release (overlay `guidelines.yaml`, ADR 0016) | Many; one set per overlay pack version | New pack version per change; cases record theirs | Shared, never deleted |
| `comments` | D | Any case member (own comments) | Many | Edit own; resolve | Case deletion; may be disclosable |
| `points` | E | Named expert | Many | State machine; stale on fact change | Case deletion |
| `range_notes` | E | Named expert | One per question | Edit freely | Case deletion |
| `relevant_facts` | E | Worker; named expert dismisses | One per question and fact | State machine | Case deletion |
| `witness_claims` | E | Worker; named expert confirms | Many per statement | State machine | Case deletion |
| `witness_comparisons` | E | Named expert asks; worker finishes | One per request | State machine | Case deletion |
| `opinions` | E | Named expert only | One per question | ETag on every save; versions kept | Case deletion |
| `opinion_versions` | E | System on save | Many | Append only | Case deletion |
| `lint_issues` | E | Worker; named expert dismisses | Many | Recalculated on save; dismissals kept | Case deletion |
| `report_versions` | F (table and lock trigger: Foundation) | Named expert builds, signs, serves | Many per case | Locked after signing (database refuses changes, 409 `locked`); withdrawn never deleted | Case deletion only |
| `report_snapshots` | F | System at signing | Several per signed version | Never change | Case deletion |
| `report_paragraphs` | F | System at build | Many | Locked with the version | Case deletion |
| `citations` | F | System at build from opinions | Many | Snapshot of the fact; "changed since cited" check | Case deletion |
| `report_servings` | F | Named expert | One per recipient | Never edited; corrections are new rows | Case deletion |
| `final_check_runs` | F | System | Many | Append only | Case deletion |
| `spot_checks` | F | Named expert sends; colleague answers | Many | Link expires after 7 days; five wrong codes lock it; answers kept once | Case deletion |
| `spot_check_address_tries` | F | System (the colleague's signed-out routes) | One per address | Tries counted in a 15-minute window; over 20 refused (429) | Deleted a day after its window starts |
| `report_settings` | F | Named expert or helper (`report.build`) | One per case | Editable | Case deletion |
| `report_section_texts` | F | Named expert (`opinion.write`) | One per section | Editable with If-Match; frozen into each build | Case deletion |
| `report_builds` | F | System at build | One per draft | Never change | Case deletion |
| `time_entries` | F | Person who worked; named expert adjusts | Many | Editable until invoiced | Case deletion; invoice keeps totals |
| `time_estimates` | F | Anyone with `invoice.create` (named expert) | Many per step; newest counts | Append-only (never changed or deleted) | Case deletion; invoice keeps the amount |
| `invoices` | F | Issuer (named expert or org admin) | Many | Immutable once sent; state machine | Kept for tax beyond case deletion as metadata only (no patient data) |
| `credit_notes` | F | Issuer | Many per invoice | Immutable | As invoices |
| `invoice_payments` | F | People who record payments on the case | Many per invoice | Insert only; their sum is the invoice's paid amount, never above total less credit notes | Goes with its invoice (cascade) |
| `court_directions` | G | Named expert or assistant | Many | Append only | Case deletion |
| `deadlines` | G | System from pack or direction; named expert edits | Many | Every change in `deadline_changes` | Withdrawing the case cancels open ones (`cancelled_at`, then frozen); closed cases get no new pack deadlines; case deletion |
| `deadline_changes` | G | System | Many per deadline | Append only | Case deletion |
| `hearings` | G | Named expert or assistant | Many | State machine; adjournment links the new date | Case deletion |
| `written_question_rounds` | G | Named expert or assistant | Many | State machine | Case deletion |
| `written_questions` | G | Named expert answers | Many per round | State machine; locked when served | Case deletion |
| `opposing_reports` | G | Named expert or assistant registers; worker checks | One per document | Checked once; checked_at set by the worker | Case deletion |
| `opposing_claims` | G | Worker; named expert confirms | Many | State machine; result hidden until the viewer has read our page | Case deletion |
| `report_changes` | G | Named expert | Many | Go into a supplementary report | Case deletion |
| `expert_meetings` | I | Named expert | Many | State machine | Case deletion |
| `expert_meeting_participants` | I | Named expert | Many per meeting | Add or remove before signing | Case deletion |
| `agenda_versions` | I | Named expert or assistant | Many per meeting | Append only | Case deletion |
| `agenda_items` | I | Named expert | Many per agenda version | Own positions editable until signed | Case deletion |
| `court_bundles` | I | Named expert or assistant | Many versions | State machine; superseded kept | Case deletion |
| `concordance` | I | Worker; named expert confirms | One set per court bundle version | Recomputed per version | Case deletion |
| `trial_pack_settings` | I | Named expert | One per case | Edited with If-Match | Case deletion |
| `readiness_replies` | I | Named expert writes; case members read | One per question | Rewritten in place, version counted | Case deletion |
| `trial_notes` | I | Named expert | One per distinct sentence | Choice may change; never sent | Case deletion |
| `trial_packs` | I | Named expert | Many per hearing | Stale when new material arrives | Case deletion |
| `offline_packages` | I | Named expert | One per device per hearing | Expire; revocable; stale when new material arrives | Expire; deleted with the case |
| `legal_holds` | H | Named expert or org owner | Many; any active blocks deletion | State machine | Released before deletion can run |
| `deletion_certificates` | H | System | One per deleted case | Never change | Kept (no patient data) |
| `deletion_ledger` | Foundation (H writes, through `app.purge_case`) | System | One per deleted case | Append only; replayed after restore (`app.replay_deletion_ledger`) | Kept |
| `case_keys` | Foundation | System only | One per case | Never updated; destroyed at deletion by `app.destroy_case_key` (worker, in the case's own job) | Destroyed at deletion |
| `jobs` | Foundation | System | Many | State machine; one case-changing job at a time per case; waits while its case is on hold; requested_by set by the system; step counts are whole numbers, never text; a failed processing job goes back to queued only through "Try again" (`app.requeue_failed_job`, slice A), which counts attempts again from none | Metadata kept 90 days |
| `job_kind_actions` | Foundation | Migrations only | One per job kind | Read-only reference | Kept (no patient data) |
| `job_steps` | Foundation | Worker | Many per job | Insert only; a finished step is skipped on retry | With the job |
| `scheduled_runs` | Foundation | Worker | One per schedule per date | Insert only; claimed once per date | Kept (no patient data) |
| `uploads` | Foundation | Case uploaders (`case.upload`) | Many per case | State machine (open → complete or expired); chunks only while open; daily sweep expires unfinished uploads after 7 days and deletes their chunks; completing publishes `DocumentUploaded` (with `append`) in the same transaction | Case deletion |
| `notifications` | Foundation | System; the person marks read | Many | Read state only; held while the case is on hold | Deleted after 1 year; case ones with the case |
| `feature_flags` | Foundation | Avodeck staff | One per key per scope | Edit; audited | Removed when the feature ships |
| `audit_log` | Foundation | System (append only) | Many | Hash-chained | Pseudonymised metadata kept 6 years after case deletion |
| `idempotency_keys` | Foundation | System | One per person per key | Replaced only after it expires | Expired after 24 hours |
| `ai_calls` | Foundation | System | Many | Append only, no content | Metadata kept 13 months |
| `transcripts` | Foundation (dictation kit) | The speaker | Many | Kept only if the expert keeps them | Case deletion |
| `may_matter` | G | Worker; named expert marks seen | Many | Seen state only | Case deletion |
