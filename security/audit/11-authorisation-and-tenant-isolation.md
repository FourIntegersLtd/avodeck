# 11. Authorisation and tenant isolation

Assessment date: 10 November 2026. Branch: `audit/tenant-isolation`. Scope: who can reach whose data, through every route, the database, file storage, the worker, callbacks and after access is taken away.

## 1. Summary

- **No path was found by which a person in one organisation can read or change another organisation's case, or a case in their own organisation that they have no access to**, through any HTTP route, download, job, event stream, idempotency replay, webhook or reviewer link. This was tested, not read: 4,316 automated attack and control tests pass against a local database with two organisations.
- **Three weaknesses at the database layer were confirmed and are fixed in this branch** (L1, L3, L4). They could not be reached through the API as it stands; each needed a SQL statement run as the API's database role, so they were a missing second line of defence. L1 would have exposed another organisation's pages.
- **One weakness in the outside reviewer link was confirmed and is fixed** (NEW-3: the link kept working after the case was withdrawn or deleted, or the report version withdrawn).
- **Eleven smaller confirmed weaknesses remain open** (section 7). None crosses a tenant boundary to read case content. The most important are that an open event stream or dictation socket keeps running after access is revoked, and that sending a reviewer link does not ask for a fresh security check.
- **Twelve test results are behaviours the written permissions allow** but that an owner should decide on (section 8), chiefly: an organisation admin can add themselves to any case in the organisation and then read it.
- Nothing in production or the cloud database was contacted. Items that depend on cloud settings are listed as unverified (section 11).

Every claim in this document rests on a test in `api/tests/security/` or a cited line of code or migration. Design documents were used only to decide whether a measured behaviour is intended.

## 2. Verified access-control model

### Principals and relationships

| Thing | Where | Notes |
|---|---|---|
| Person | `app.people` (id is the auth user id) | `closed_at` marks a closed account |
| Organisation | `app.organisations` | `personal` or `team` |
| Membership | `app.memberships` | owner, admin, expert, assistant, viewer; `ended_at` ends it |
| Case | `app.cases` | `organisation_id`, `named_expert_id`, `bundle_id` |
| Case member | `app.case_members` | co_expert, supervisor, assistant, viewer |
| Support staff | `app.support_staff`, plus a time-limited grant from the named expert | metadata only without a grant |
| Records custodian | custody transfer tables | keeps served cases reachable after an organisation closes |
| Outside reviewer | spot-check token and code, no account | sees only the cited facts of one report version |

There is no impersonation feature. A user is never "in" a case by organisation membership alone: access comes from being the named expert, a case member, or an organisation owner or admin (administrative actions only).

### Where the decision is made

- SQL: `app.case_access` (`supabase/migrations/0032…:126-181`), `app.case_action_allowed` (`0200…:7-68`), `app.can_access_bundle` (`0041…:12`) for pages and documents.
- Python: `api/app/core/auth/access.py` (`require_case_access`), `org_access.py`, and the action table `CASE_ACTIONS` in `permissions.py`.
- Row-level security repeats the check on every case table through `accessible_case_ids` and `can_access_bundle`.

### Runtime identities

- The API connects as `app_api` and sets `app.person_id` from the session on each request. It cannot set the worker's variable (tested).
- The worker connects as `app_worker` and sets `app.job_case_id` per job; rows of other cases are invisible to it (tested).
- The service-role key is used only for auth administration calls, never for case data.

## 3. Enforcement by layer

| Layer | Status | Evidence |
|---|---|---|
| API routes | Enforced | every route walked as four outside personas, section 5 |
| Secondary ids (page, fact, document, version, invoice, export, member…) | Enforced | own case id in the path with the victim's sub-id, and victim ids in bodies: refused |
| Database (RLS as `app_api`) | Enforced, with the gaps in L9 | select, insert, update, delete per sensitive table |
| Database tenant columns, identity columns, audit rows | Was missing; fixed here | L1, L4, L3 |
| File storage and downloads | Enforced | real sealed files in a mock S3 store; 175 download tests |
| Worker | Enforced, one low gap | NEW-1 |
| Jobs, retries, idempotency replay | Enforced | replay is per person; job routes check the job's case |
| Event streams and dictation | Enforced at connect; revocation lags | NEW-2, L8 |
| Callbacks (e-sign webhook) | Signature enforced; hash not bound | L6 (policy) |
| Outside reviewer link | Enforced; closed-case gap fixed here | NEW-3 |
| After revocation (member removed, membership ended, case reassigned, organisation closed, session revoked, grant expired) | Enforced on the next request | section 6 |

## 4. Endpoint coverage

The application exposes 351 route-and-method pairs. The matrix builds its list from `app.routes` at run time, so a new route is covered without editing the tests.

| Group | Count |
|---|---|
| `/v1/cases/{case_id}/…` | 246 |
| `/v1/me…` | 36 |
| `/v1/organisations…` | 17 |
| `/v1/auth…` | 11 |
| `/v1/support…` | 11 |
| uploads, jobs, notifications, spot-checks, legal agreements, others | 30 |

Each route is called as: the other organisation's owner, the other organisation's expert, a person with only a personal organisation, and support staff with no grant. Expected: 404 for outsiders (403 where a role is in the case but lacks the action), never a 2xx, never a 5xx, and never a body containing a marker written into the victim's rows. Every refusal has a control: the victim's named expert calls the same route and is let through, and the attacker reaches the same route in their own tenant.

## 5. Test matrix

All files are in `api/tests/security/`, helpers in `api/tests/support/isolation_*.py`. Final run: **4,316 passed, 261 skipped, 24 expected failures, 0 failures** (before the second pass on L1/L3/L4, which adds tests; the database file alone is then 1,528 passed, 8 skipped, 4 expected failures).

| File | What it attacks | Result |
|---|---|---|
| `…_http_routes.py` | every route, four outside personas, plus in-team roles against the permissions table | no cross-tenant success |
| `…_cross_case_ids.py` | own case in the path, victim's sub-resource id; victim ids in bodies | all refused |
| `…_escalation.py` | change own role, set owner, invite above own role, accept another's invite, mass-assignment keys, L2, L5, L7, L10 | role escalation refused; L2, L5, L10 as sections 7–8 |
| `…_db_layer.py` | SQL as `app_api` and `app_worker` against victim rows; function privileges | L1, L3, L4 fixed; L9 open |
| `…_downloads.py` | page image, original, report, invoice, export, trial pack, certificate with real sealed files | all refused to outsiders |
| `…_async_and_revocation.py` | jobs, streams, idempotency, webhook, reviewer link, and every "after access ends" case | NEW-1, NEW-2, L8 open; NEW-3 fixed |

A test that proves a weakness asserts the safe behaviour and is marked as a strict expected failure with its finding id (`AUDIT-11 <id>`, or `AUDIT-11 POLICY <id>` where the written permissions allow it). It turns into a failure the day the weakness is fixed, so the mark cannot be left behind.

## 6. After access is taken away

Measured on the next request after each event: case member removed, membership ended, case reassigned to another expert, organisation closed, session revoked, support grant expired. In every case the next request is refused. The exceptions are connections already open (NEW-2, L8) and a closed account, whose session stays valid: it can still read its served cases (intended, for records custody) and gets 404 on unserved ones.

## 7. Confirmed findings

### Fixed in this branch

| Id | Severity | Finding | Fix |
|---|---|---|---|
| L1 | High (defence in depth) | As `app_api`, a case's `bundle_id` or `organisation_id` could be changed, or a new case created on another organisation's bundle. `can_access_bundle` then exposed that bundle's pages (10 victim pages readable in the test). Not reachable through any API route. | `supabase/migrations/0067_foundation_tenant_isolation_db_layer.sql`: triggers refuse the update, and an insert that supplies a `bundle_id` |
| L4 | Medium | As `app_api`, a person could change their own email, insert their own row with any address, or reopen their closed account. Invite acceptance and custodian matching trust `people.email`. | 0067: triggers refuse insert, email change and reopening |
| L3 | Medium | The audit-log insert rule checked only the writer, so a row could name any case or organisation and appear in that case's audit view. | 0067: a case may be named only by its named expert, a present or past case member, or someone with current access; an organisation only by a present or past member; support staff exempt |
| NEW-3 | Medium | The reviewer link still opened after the case was withdrawn or deleted, or its report version withdrawn. | `supabase/migrations/0609_report_spot_check_closed_case.sql`: answers "expired", no attempt counted, refusal still audited |

Both migrations were reviewed independently. The first version of 0067 guarded updates only; the review found the insert routes, each was reproduced with a failing test and then closed. Both apply safely to a database that already holds later migrations, and neither is redefined by a later one.

Known limits of the fixes: an audit row naming both a case and an organisation does not check that the case belongs to that organisation; a former organisation member can still write an organisation-only audit row (needed for "left the organisation", which is recorded after the membership ends); a withdrawn case that is manually archived within the link's 7 days reopens the link (`0609…:26`).

### Open

| Id | Severity | Finding | Evidence |
|---|---|---|---|
| NEW-2 | Medium | An open event stream keeps sending after the session is revoked, until the stream's time limit. The periodic re-check asks about case access only. | `api/app/core/sse.py:46-66` |
| L8 | Medium | Dictation checks the session and case access only at connect. A removed member, or a revoked session, keeps receiving text for the audio they send. They receive only their own speech back, not case data. | `api/app/core/routes/dictation.py:70-75` |
| L5b | Medium | Starting a reviewer check emails cited facts to an outside address without step-up: it works after a password-only login and with MFA 30 minutes old. Rule 10 names "send". | `api/app/features/report/routes_spot_checks.py:69-73` |
| L9a | Low | `app.mark_email_bounced` can be called by any signed-in person for any address, stopping that person's email. | `0009…:143` |
| L9b | Low | `app.case_ever_served` and `app.expert_has_served` answer any signed-in person about any case or expert (yes/no only). | `0012…:73`, `0360…:8` |
| L9c | Low | Any signed-in person can insert an `app.bundles` row naming another case as its source. The row gives no access. | `0040…:106` |
| NEW-1 | Low | A worker context holding one case's job id and another case's id can write a `job_steps` row. Needs worker code to pass mismatched ids. | `0007…:664-669,711-712` |
| NEW-4 | Bug, not security | The named expert's `DELETE /v1/cases/{id}/documents/{document_id}` answers 404 and the row stays: there is no delete rule for `app_api`. | `0042…:38-45`, `api/app/features/pipeline/repo.py:1239` |

## 8. Privilege-escalation paths

Tested and refused: changing one's own role, making oneself owner, inviting at a role above one's own, accepting an invite addressed to someone else, and sending extra keys to the update routes.

Allowed by `docs/spec/permissions.md`, for an owner decision:

| Id | Behaviour | Where |
|---|---|---|
| L2 | An organisation admin with no right to read a case adds themselves as a case member (any role) and can then read it. The action is audited. | `api/app/features/setup/routes_case.py:111`, permissions.md:34 |
| L2b | An organisation admin makes themselves named expert of an unserved case (step-up required, audited). | `api/app/features/organisations/routes_cases.py:42`, `service.py:280-315` |
| L5a | A case assistant may start a reviewer check to an outside address (measured: 201). | permissions.md:49, 66 |
| L10 | A case member with upload rights can write to, complete or cancel a colleague's open upload on the same case. | `api/app/core/routes/uploads.py:140-153` |
| L6 | The e-sign callback is trusted on its signature; the document hash in it is not compared with the prepared package. | `0900…:393` |
| NEW-3 (rest) | The reviewer link still opens after a case is settled or archived. | `0609…:25` |

L2 is the one that matters: within an organisation, an admin can reach any case's content in two steps. If that is not intended, require the named expert's consent, or notify them.

## 9. Leads cleared

- L7: reading invoices, time entries and bank details by low roles matches the permissions table. The organisation bank-details read has no step-up (`api/app/features/report/routes_bank.py:70-85`); reading is not in rule 10.
- `app.purge_case_rows` is executable by neither runtime role; `purge_case`, `destroy_case_key` and `cases_due_for_deletion` by the worker only.
- `bundles_view` names an action that does not exist (`0040…:99-104`); it fails closed.
- Support staff finding a case by id and resuming or requeuing a job is by design and returns metadata only.
- Idempotency replay is per person; invite acceptance is bound to the email, hashed and single use.

Smaller observations: changing email does not end other sessions (`api/app/features/organisations/account.py:92-138`); new functions get execute rights for `app_api` by default (`0000…:37-48`), so every new security-definer function needs an explicit caller check.

## 10. Limits of the tests

- 261 skips. About 60 are write routes where the seeded placeholder data makes the owner's own request fail, so the "owner is let through" control cannot run; the attacker side of those routes still runs. The rest are personas or parameters that do not apply to a route.
- The file store is a local mock of S3 with real per-case sealed files. The real bucket policy was not tested (audit document 12).
- The event-stream re-check interval in production is about 30 seconds by default; the tests shorten it.
- No test was run against the cloud database or any deployed service.

## 11. Unverified

| Item | Evidence needed |
|---|---|
| Cloud auth settings (open sign-up, email confirmation) that the invite binding relies on | the project's auth settings, read by the owner |
| That the cloud database's grants and policies match the migrations | a schema diff of the cloud database against a fresh local one |
| Production environment values (stream limits, feature flags for support routes) | the deployed configuration, values not printed |
| Whether the e-sign provider sends the signed document's hash | the provider's callback specification |

## 12. Remediation, in priority order

1. Merge this branch: applies 0067 and 0609.
2. L5b: require step-up on starting a reviewer check.
3. NEW-2 and L8: re-check the session, not only case access, on the stream timer; re-check both periodically on the dictation socket.
4. L2: decide whether an admin may self-add to a case; if not, add consent or notification.
5. L9a–c: add a caller check to the three functions and tighten the `bundles` insert rule; revoke default execute on new functions.
6. NEW-1: make `job_steps` check that the job belongs to the case.
7. NEW-4: add the delete rule so the expert can remove a document.
8. L6, L10, L5a, NEW-3 (settled or archived): owner decisions; no change needed if intended.
