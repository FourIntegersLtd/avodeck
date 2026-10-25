# Permissions

The single source of truth for who may do what. `core/auth/require_case_access(action)` implements this table and `api/tests/guards/test_permissions.py` tests every cell. Row-level security mirrors it. If a new endpoint needs an action not listed here, add the row first (foundation approval).

## Two kinds of role

- **Organisation role** (`memberships.role`): what someone can do for an organisation as a whole.
- **Case role** (`case_members.case_role`, or being the case's `named_expert_id`): what someone can do on one case.

A person's rights on a case are the **union** of their case role and the administrative rights of their organisation role. Seeing patient content always needs a case role (named expert or case member); organisation owners and admins see case metadata only unless also added to the case.

## Case actions

✓ = allowed · — = not allowed (404 if they can't see the case at all, 403 if they can see it but not do this)

| Action | Named expert | Co-expert | Supervisor | Assistant | Viewer | Avodeck support (under a grant) | Records custodian (after a transfer) | Org owner/admin (not on case) |
|---|---|---|---|---|---|---|---|---|
| `case.view_metadata` (label, dates, status) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `case.view_content` (pages, facts, opinions) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (every view audited) | ✓ | — |
| `case.edit_setup` (parties, instructing parties, links (the linked case must also be one the person can see), hearings, directions, prefill suggestions) | ✓ | — | — | ✓ | — | — | — | — |
| `case.upload` (records, instructions, exhibits) | ✓ | — | — | ✓ | — | — | — | — |
| `case.manage_members` | ✓ | — | — | — | — | — | — | ✓ |
| `case.reassign_named_expert` (before serving; after serving, create a replacement case instead) 🔒 | — | — | — | — | — | — | — | ✓ |
| `case.hold` (put on hold, resume) | ✓ | — | — | ✓ | — | — | — | — |
| `case.change_jurisdiction` (jurisdiction, case type, specialty; before serving) 🔒 | ✓ | — | — | — | — | — | — | — |
| `conflict.declare` (conflict answers and declarations) | ✓ | — | — | — | — | — | — | — |
| `page.read` (counts towards citing) | ✓ | own reads only | own reads only | own reads only | — | — | — | — |
| `page.quarantine`, `page.replace` (also splits a two-page scan), `page.rotate` | ✓ | — | — | ✓ (suggest only; named expert confirms quarantine) | — | — | — | — |
| `page.review_duplicate` (set a page aside as a copy, or bring it back) | ✓ | — | — | ✓ | — | — | — | — |
| `page.transcribe` (type out a page we couldn't read, ask for a second reading, mark relevance or ask for a clearer copy) | ✓ | — | — | ✓ | — | — | — | — |
| `fact.verify` (also flag a fact for points against, move it to a clearer copy) | ✓ | — | — | — | — | — | — | — |
| `link.suggest` (ask for suggested links between entries and the questions) | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `link.decide` (accept or reject a suggested link), `finding.decide` (keep or dismiss a thing to look at; run the checks again), `availability.override` (correct when an entry reached the clinician), `consultation.note` (private notes on one consultation) | ✓ | — | — | — | — | — | — | — |
| `question.confirm` | ✓ | — | — | — | — | — | — | — |
| `opinion.write` | ✓ | — | — | — | — | — | — | — |
| `comment.write` | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `report.build` | ✓ | — | — | ✓ | — | — | — | — |
| `report.sign` 🔒, `report.serve` 🔒, `report.withdraw` 🔒 | ✓ | — | — | — | — | — | — | — |
| `request.draft` (instructing-party emails; missing-item status: not available, open again, received) | ✓ | — | — | ✓ | — | — | — | — |
| `request.send` 🔒 | ✓ | — | — | — | — | — | — | — |
| `invoice.create` (draft) | ✓ | — | — | ✓ (draft only) | — | — | — | ✓ (team cases) |
| `invoice.send` 🔒, `credit_note.issue` 🔒 | ✓ | — | — | — | — | — | — | ✓ (team cases) |
| `invoice.record_payment` | ✓ | — | — | ✓ | — | — | — | ✓ |
| `case.export` 🔒 | ✓ | — | — | — | — | — | — | ✓ (administrative copy: report versions, invoices, logs; no page images) |
| `case.retention` 🔒 | ✓ (within org policy) | — | — | — | — | — | — | ✓ |
| `legal_hold.set` 🔒, `legal_hold.release` 🔒 | ✓ | — | — | — | — | — | — | owner only |
| `case.withdraw` 🔒 | ✓ | — | — | — | — | — | — | — |
| `case.settle`, `case.archive`, `case.reopen` | ✓ | — | — | — | — | — | — | — |
| `case.view_audit` (the case's audit trail) | ✓ | — | — | — | — | ✓ | ✓ | ✓ |
| `case.view_certificate` | ✓ | — | — | — | — | — | ✓ | ✓ |
| `support_access.grant` 🔒 | ✓ | — | — | — | — | — | — | — |
| `case.delete_now` 🔒 | ✓ (unserved only) | — | — | — | — | — | — | owner only (unserved only) |

Slice F uses existing actions: adjusting time and giving the time estimate are `invoice.create`; writing off an invoice also needs `credit_note.issue`; starting and listing colleague spot-checks is `report.build`. Downloading a signed (locked or served) version is `case.view` plus step-up (403 `step_up_required` without MFA in the last 10 minutes), because a signed report leaving Avodeck is an export; drafts download without step-up. Writing off an invoice also needs step-up. Bank details on a billing profile: the expert changes their own, an organisation owner or admin the organisation's (anyone else 404); every change needs step-up. The colleague is not a person: they reach a spot-check only with the link token and the emailed code, through `app.spot_check_unlock` / `app.spot_check_respond` (migration 0604), each attempt audited with no person (`spot_check.opened`, `spot_check.refused`, `spot_check.answered`).

**Public, token-gated routes.** `GET /v1/spot-checks/{token}` and `POST /v1/spot-checks/{token}/responses` take no session and no `require_case_access` (there is no case session): the link token and the emailed code, kept only as hashes, are the gate. They answer 200 with a `status` for every outcome and commit each wrong code, so five wrong codes lock the link. There is no per-address rate limit yet (core has no limiter); the five-tries lock per link is the limit.

After the named expert leaves the organisation, they keep every named-expert action on their named cases; the organisation keeps its "not on case" column.

A deleted case (status `deleted`, the tombstone `app.purge_case()` leaves) answers only `case.view_certificate`, for the same people as above (the named expert and organisation owners/admins). Every other action on it, and the certificate for anyone else, is 404, exactly as for a case that never existed (`DELETED_CASE_ACTIONS` in `core/auth/access.py`; `app.case_access(case_id, action)` and `app.can_access_case()` in migration 0018). Lists and Today never show it.
## Checks the database repeats (rule 9, ADR 0013)

Row-level security lets a person update a row; triggers then check each change against the action it is, for the person `app.person_id` names:
- `report_versions.status`: → locked needs `report.sign`, → served `report.serve`, → withdrawn `report.withdraw`; any other edit `report.build`.
- `cases.named_expert_id`: `case.reassign_named_expert`, and only while the case has never been served (no served or withdrawn report version, status not served or settled).
- `cases.retention_policy`, `delete_after`: `case.retention`.
- `cases.on_hold`: `case.hold`.
- `cases.jurisdiction`, `case_type`, `specialty`, `pack_versions`: `case.change_jurisdiction`, and only while the case has never been served.
- `instruction_versions.locked_at` (locking the questions): `question.confirm`; a lock is never undone (a new instruction version reopens the questions).
- Creating a case: the creator is an owner, admin or expert of the organisation and names themselves, or an assistant naming an owner, admin or expert member; an assistant who creates a case joins it as its assistant.
- `cases.status`: → deleted `case.delete_now` (never-served cases only), → withdrawn `case.withdraw`, → served `report.serve`, → settled `case.settle`, → archived `case.archive`, settled or archived → active `case.reopen`; any other move and every other column `case.edit_setup`.
- `app.after_serving_cancel_deadlines()`: `case.withdraw`, only once the case is withdrawn; sets `deadlines.cancelled_at` on every open deadline (called from slice C's withdraw through the `CaseWithdrawn` event).
- `app.purge_case()`: only the worker, inside the case's own job (`app.job_case_id`); refused while the case is on hold or under an active legal hold.
- `missing_items`: status, reason and received page change only with `request.draft`; the description and the page it cites never change; the worker inserts.
- `app.enqueue_job()`: the action comes from `job_kind_actions` by job kind (unknown kinds need `case.upload`); callers cannot pass one. Slice G's `find_dependents` and `build_supplementary` need `opinion.write` (only the named expert; migration 0701). `jobs.requested_by` is the signed-in person, set by the database; nobody sets it by hand.
- `fact_flags` `recheck` and `fact_verifications` `remapped` rows: only the worker inside the case's own job, with no person; the expert's `fact.verify` removes a recheck flag by checking the fact again.
- `app.set_fact_availability()`: changes only `facts.available_at` and `availability_basis` (no new fact version, no status change); the worker inside the case's own job, or `availability.override`.
- `app.release_case_hold()`: `case.hold` (or the case's own job), and only once the case is off hold (409 otherwise).
- `app.case_member_names()`: `case.view_metadata` (or the case's own job); anyone else gets no names.
- An export (`export_case`) is downloaded only by the person who asked for it (`jobs.requested_by`); anyone else gets 404.
- `app.case_access(case_id, action)` and `app.can_access_case()` (migration 0030): the `support` role (current `support_staff`, a live grant from the named expert) and closed accounts, as in "Closed accounts" and "Avodeck staff" below; `app.case_role_allowed()` adds `support` to the matrix.
- `cases.named_expert_id` changing (before serving only; the reset itself refuses a served case for any role) puts every checked fact of the case back to `suggested` (`cases_named_expert_reset`, migration 0031): verifications are personal, so the new named expert checks for themselves. `facts_guard` (0212) lets that move through only from inside the reset, and holds the case row while a fact is checked, so a check and a reassignment wait for each other; a check that waited is refused if its person is no longer the named expert.
- `app.create_replacement_case()` (migration 0033): `case.reassign_named_expert` on a served case (else 404; a case never served is 409 `not_served`); the new named expert is a current owner, admin or expert member other than the case's own (else 422). The caller does not join the new case. `case_replacements` is written only by that function and never changes.
- `custody_transfers` (migration 0032): recorded only by current support staff in their own name, for a served case, from its current named expert, to the custodian the expert named or the case's own organisation, never to a staff member; only `revoked_at` changes, once, by staff. Seen by staff, the person it names and whoever may see the case's audit trail.
- `support_access_grants`: given only with `support_access.grant`, in the giver's own name, for at most 24 hours from now; only `revoked_at` changes, with `support_access.grant`, and never back. `support_staff`: the running app only reads its own row.
- `app.organisation_policy(org_id)`: a current member, someone who can see one of the organisation's cases, or that case's own job; anyone else gets nothing.

## Organisation actions

`core/auth/org_access.py` `require_org_access(action)` implements this table (the organisation id comes from the route) and `api/tests/guards/test_foundation_org_access.py` tests every cell. Someone who is not a current member of the organisation (never joined, left, or whose account is closed) gets 404; a member whose role lacks the action, 403. 🔒 actions need step-up. Qualifiers in brackets are enforced by slice O's service, not by the access check.

| Action | Owner | Admin | Expert | Assistant | Viewer |
|---|---|---|---|---|---|
| `org.view_members` (members and their roles) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `org.manage_members` 🔒 (invite, change role, remove) | ✓ | ✓ (not owners) | — | — | — |
| `org.create_case` (create a case for the organisation) | ✓ | ✓ | ✓ | ✓ (named expert must be an expert member) | — |
| `org.view_all_cases` (every case's metadata) | ✓ | ✓ | own and assigned | assigned | assigned |
| `org.set_policy` 🔒 (required template, retention limit) | ✓ | ✓ | — | — | — |
| `org.billing` 🔒 (billing profile) | ✓ | ✓ | — | — | — |
| `org.close` 🔒 (close the organisation) | ✓ | — | — | — | — |
| `org.export` 🔒 (organisation-wide export of administrative data) | ✓ | — | — | — | — |

"own and assigned" and "assigned" are not `org.view_all_cases`: those members see a case only through their case role (`require_case_access`).

Slice O's routes and the action each checks: viewing an organisation, its members and leaving it are `org.view_members`; renaming it and its policy are `org.set_policy` 🔒; members and invites (list, send, cancel, change a role, end a membership) are `org.manage_members` 🔒; the team billing profile is `org.billing`, with step-up only when the tax number changes (architecture 5.4); closing it is `org.close` 🔒. On these 🔒 routes the step-up check runs before the organisation is looked up (one answer, 403 `step_up_required`, for every organisation, so it reveals nothing); with recent MFA a non-member still gets 404. Changing the named expert is `case.reassign_named_expert` 🔒 (organisation owner or admin, before serving only; the new expert is a current owner, admin or expert member). The database repeats these: `app.is_org_admin()`, the `memberships_actor` trigger (only an owner changes an owner's role or makes an owner; nobody else ends someone else's membership), `invites` row-level security (owner or admin; only an owner invites an owner), `app.accept_invite()` (the invited email only) and `app.close_organisation()` (a current owner only), migrations 1000-1005.

## Person actions (no case)

Only the person themselves: profile, expert profile, billing profile (changing the tax number 🔒), signature, declarations, sessions, recovery codes, email change 🔒, notification preferences, custodian 🔒, account closure 🔒. Organisation admins cannot reset another person's MFA or password: no route takes another person's id, and recovery needs the person's own password as well as a recovery code. Current Avodeck support staff with no expert record get 404 on the expert profile routes (`/me/profile`), so a support account never reaches or saves the expert profile form; staff who also have an expert record are unaffected.

## Closed accounts

A person whose account is closed (`people.closed_at` set, scenario S15) keeps sign-in for the served cases on which they are the named expert, read-only: on a case that has been served (status served or settled, or any report version ever served) they may take only `case.view_metadata`, `case.view_content`, `case.view_audit` and `case.view_certificate` (`CLOSED_ACCOUNT_ACTIONS` in `core/auth/access.py`). Every other action, every other case and every organisation action is 404, exactly as for a case they cannot see. `app.case_access(case_id, action)` (migration 0030) applies the same rule, so row-level security repeats it.

## Avodeck staff

Avodeck staff are listed in `app.support_staff` (added and removed by hand in SQL, `docs/ops/support-staff.md`; no API route writes it). No case content: metadata through the support console. The case role `support` applies only while the person is current staff **and** the case has an active `support_access_grants` row (not revoked, `expires_at` in the future, checked on every read; at most 24 hours after it was given) given by the case's named expert, for that staff member or for any staff member when `granted_to` is empty. Under it support may take only `case.view_metadata`, `case.view_content` and `case.view_audit`, never a write. Every request under the support role writes `support_access.viewed` to the case's audit trail, and needs MFA completed in the last 10 minutes (`aal2`) on every call. The support console's read-only lookups (`GET /v1/support/...`, `require_support_lookup`) need current staff in a session whose MFA was completed (403 `mfa_required` otherwise, with `mfa_enrolled` so the web app asks for the code or sets it up; since ADR 0025 this is met at the first support call, not at sign-in), within the 30-minute idle and 12-hour limits, but not the 10-minute window; so does every support route that changes something (`POST`/`PUT`/`PATCH`/`DELETE` under `/v1/support`, `require_support_staff`), which also needs the CSRF token: no support action asks for a fresh MFA code (ADR 0020, 0023). The console (`GET /v1/support/cases`, `GET /v1/support/cases/{case_id}`) shows metadata only, through `app.support_cases()` and `app.support_case_grants()` (migration 1006), which answer current staff and nobody else; each look at a case, and each case a search returns, writes `support.case_looked_up` to its audit trail. The case look also says where the case is stuck (jobs, pages, suggestions, questions asked, report, closing, offline copies, named expert changes, deadlines) through `app.support_case_diagnostics()` (migration 1011), which answers current staff only, in counts, codes, statuses, timestamps and ids: never a name, the case label, page text, a fact's value, a question's words, an identifier or a date of birth (rule 6; `tests/guards/test_organisations_support_rule6.py` fails any free-text string in a `/v1/support/` answer). A grant is active only while it was given by the case's current named expert (so reassignment ends it), and giving one locks the case row so two at once leave one active. The worker role may run none of slice O's functions (migration 1007). Custody transfers (`POST /v1/support/custody-transfers`, flag `support_custody_transfer`, legal check) are recorded by current support staff only: to the person whose account email is the named expert's recorded records custodian (`app.support_case_custodian()`, migration 1008; 422 `no_custodian`, `custodian_account_closed`, `custodian_not_allowed` for support staff or the recorder) or to the case's own team organisation (422 `personal_organisation`; 409 `organisation_closed` once it has closed, through `app.support_organisation_closed()`, migration 1010, which answers current staff only); a case never served is 409 `not_served`; one holder at a time, so while a transfer is in force another is 409 `custody_already_transferred`. Every refused attempt writes `support.custody_transfer_refused` (target `refused:<code>`, no names or case text) to the case's audit trail in a transaction of its own (`audit.record_detached`), so it stays though the request rolls back. Support job actions (core routes, `core/routes/support.py`): `POST /v1/support/jobs/{job_id}/resume` (flag `support_job_resume`) puts a paused job back on the queue and `POST /v1/support/jobs/{job_id}/requeue` (flag `support_job_requeue`) puts a failed one back with its attempts reset; current staff only (anyone else 404; no fresh MFA code, ADR 0023), no grant needed (they show no case content), through `app.support_job_action()` (migration 0035, current staff only; the worker may not run it). The body is `{reason_code}` from a fixed list; each writes `support.job_resumed` or `support.job_requeued` to the job's case audit trail (target `job:<id>/reason:<code>`), keeps what the job was in `job_support_actions`, and sends the named expert a patient-free email (case and job ids only). Each flag is off until staff switch it on. Accounts for new people (`GET/POST /v1/support/accounts`, `POST /v1/support/accounts/{id}/temporary-password`, migrations 1012, 1013): current staff only (anyone else 404); creating and making a new temporary password need an `Idempotency-Key` but no fresh MFA code (ADR 0023); the list is a lookup. Support gives a name and email only and adds nobody to a team (a team's own owners and admins invite people). Avodeck makes a temporary password (ADR 0024), shown to support once and never stored; support tells it to the person by phone or in person. When the cell has mail, the person also gets an `account.created` email with a set-password link (1 hour), never the password. Each creation writes `support.account_created` (target `person:<id>`), each new password `support.account_password_renewed`; at most 20 creations an hour per staff member, counting refused attempts (`support.account_refused`, kept though the request rolls back), with one create at a time per staff member. A new temporary password is allowed only while the account is `invited` and the person still has the temporary password and no MFA (409 `already_active` otherwise), at most once a minute; the old one stops working and the person is signed out everywhere. A person with a temporary password (`people.must_change_password`) may use only `GET /v1/auth/session`, `GET /v1/me`, the MFA steps, sign-out and `POST /v1/auth/password/change` (5 wrong current passwords in 15 minutes give 429 `too_many_attempts`; everything else 403 `password_change_required`, in core `SessionRoute`); changing their password clears it. A grant is given, listed and revoked on the case (`POST/GET/DELETE /v1/cases/{case_id}/support-access`): giving and revoking are `support_access.grant` 🔒, listing is `case.view_metadata`; `granted_to` must be current staff; a new grant ends the case's earlier active one.

## Records custodians

When a named expert dies or can no longer act (architecture 10.1 "Death or incapacity"; legal check, ADR 0012), Avodeck support records a custody transfer of each served case (`custody_transfers`, migration 0032; slice O's `POST /v1/support/custody-transfers` behind the flag `support_custody_transfer`) to the records custodian the expert named (`people.records_custodian_email` is their sign-in email), or to the case's own organisation, whose current owners and admins then hold the records. Support staff never receive custody (nobody in support can hand a case to themselves). The case role `custodian` applies while the transfer is not revoked and the case has been served; under it the custodian takes only `case.view_metadata`, `case.view_content`, `case.view_audit` and `case.view_certificate` (`CUSTODIAN_ACTIONS` in `core/auth/permissions.py`), never a write. Someone with a case role of their own keeps it; a closed account holds no custody. `app.case_access(case_id, action)` applies the same rule, so row-level security repeats it.

## Tests

- Every cell above has a test using the fixture packs and two organisations, so a pass cannot depend on gb-eaw words.
- A "second case" test: the same person as named expert on a personal case and as assistant on a team case sees the right rights on each.
- A "left the organisation" test: named-expert rights survive, assistant rights don't.
- A support test: no grant, an expired or revoked grant, a grant for another staff member or someone no longer staff all see nothing (404); every view under a grant is audited.
- A closed-account test: read-only on served named cases, 404 everywhere else.
- A custodian test (`tests/guards/test_foundation_custody_access.py`): read-only on the served case transferred to them (or to their organisation, for its owners and admins), 404 without a transfer, after revocation, on any other case or with a closed account.
