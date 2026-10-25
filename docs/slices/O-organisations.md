# Slice O: Organisations and accounts

Owner: Claude session, branch `slice/o-organisations` · Phase: 2 · Migrations 1000–1099

## Goal
Agencies, chambers and NHS teams can work in Avodeck together. One person, one sign-in, can be a solo expert and a member of any number of teams; each case belongs to one organisation, has one named expert, and everyone else sees only what their role allows. People can leave, cases can be reassigned before serving, and accounts and organisations can close without losing anything a court may still need.

## Screens
- `S27-OrgMembers` Organisation and members (canvas board 32): members, roles, invites, rules, billing, closure
- `S28-Account` Account and security (canvas board 33): organisations, leave, custodian, close account (devices, codes and messages built by foundation)
- Invite accept page: reuse the sign-in layout (`S01-SignIn`)
- Today: organisation badge and filter (extends foundation's S03)
- Case header: "Who is this case for?" and named expert (with C)

## Endpoints (architecture 5.4)
- `GET/POST /v1/organisations`; `GET/PATCH /v1/organisations/{org_id}`; `PUT /v1/organisations/{org_id}/policy` 🔒
- `GET /v1/organisations/{org_id}/members`; `PATCH/DELETE /v1/organisations/{org_id}/members/{membership_id}` 🔒
- `GET/POST /v1/organisations/{org_id}/invites` 🔒; `DELETE /v1/organisations/{org_id}/invites/{invite_id}`; `POST /v1/invites/{token}/accept`
- `POST /v1/organisations/{org_id}/leave`; `POST /v1/organisations/{org_id}/close` 🔒
- `GET/PUT /v1/organisations/{org_id}/billing-profile`; `GET/PUT /v1/me/billing-profile`
- `POST /v1/cases/{id}/named-expert` 🔒 (before serving; 409 after)
- `PUT /v1/me/custodian` 🔒; `POST /v1/me/close` 🔒
- `GET/POST /v1/cases/{id}/support-access` 🔒 (POST); `DELETE /v1/cases/{id}/support-access/{grant_id}` 🔒
- Support console: `GET /v1/support/cases?q=`, `GET /v1/support/cases/{case_id}` (staff, MFA completed at sign-in for the session, ADR 0020; actions keep the 10-minute step-up); the case page `/support/cases/:caseId` shows where a case is stuck (migration 1011, counts and codes only)
- `POST /webhooks/stripe`: Avodeck's own subscription (seats per organisation), signature-verified; moved here from the foundation

## Tables owned
`invites`, `billing_profiles` (team ones; personal ones created by F), `support_access_grants` (the table and its RLS were created by foundation in migration 0030 so the access check could be built and tested; O writes it through its routes and changes it only with foundation approval; it is kept after case deletion as the record of consent), `support_accounts` (migration 1012: accounts support created for new people). Writes team rows (and, for a support-created account, the new person's `people` row and own practice) in `organisations` and `memberships` (tables owned by foundation), `organisations.policy`, `people.records_custodian`, `people.closed_at`.

## Contracts
- **In:** foundation `people`, `organisations`, `memberships`, `case_members`, `require_case_access`, notifications. Built for O in Phase 0 (foundation, migration 0030): `require_org_access(action)` (`core/auth/org_access.py`, the organisation-action table), the `support` case role and `require_support_staff` (`core/auth/support.py`), `support_staff`, `support_access_grants`, closed-account access (S15), `org_policy()` (`core/contracts/org_policy.py`), `request_case_deletion()` and `case_export_done()` (`core/contracts/close.py`, over H's jobs), and the mails `invite_mail`, `case_reassigned_mail`, `support_access_granted_mail` (`core/email.py`).
- **Out:** team organisations and roles used by every slice's access checks; billing profiles used by F.

## Acceptance tests (write first)
Using the fixture packs and two organisations ("Solo practice" and "Agency"):
- [ ] S1: one sign-in; Today shows both organisations' cases with badges and a filter; conflict check spans both
- [ ] S2: leaving ends access to the organisation's cases except named cases, where every named-expert action still works (F31)
- [ ] S3: reassignment works only before serving; the new expert starts with no reads or verifications; after serving it returns 409 and offers a linked replacement case
- [ ] The last owner cannot leave or be removed (F32)
- [ ] Invites expire after 7 days, work once, and only for the invited email; sending needs step-up
- [ ] Owners and admins see case metadata for all organisation cases, never page content unless added to the case
- [ ] Organisation policy can require a template and cap retention; personal defaults apply otherwise
- [ ] Organisation closure is refused while any unserved case is open; served cases survive closure until retention
- [ ] S15: account closure deletes unserved personal cases after export, keeps served cases read-only, and keeps sign-in for them
- [ ] S17: support sees metadata only; a 24-hour grant opens one case; every view is in its audit trail; the grant expires (F38)
- [ ] Billing profiles: each issuer has its own gap-free numbering
- [ ] Screens pass Playwright + axe and match their canvas boards

## Rules that apply
4, 6, 9, 10, 11 from the root `AGENTS.md`.

## Legal checks before release
Items marked **(legal check)** in architecture 10.1 and ADR 0012: named-expert access after leaving, account closure, custodian, controller and processor roles.

## Build status (Phase 1, API)
- Built (PR 1, migrations 1000-1005): teams, members, invites, leaving (F31, F32), policy, team billing profile, named-expert reassignment (S3, before serving), organisation closure.
- Built (PR 3, on foundation's 0031-0033): `POST /v1/cases/{case_id}/replacement` 🔒 through `core/contracts/replacement.py`; the 409 `served_use_replacement` points at it. The bundle copy waits for slice A (`docs/decisions/deferred.md`).
- Numbering: the policy check took 1003, so the billing policy is 1004 and closure 1005.
- Built (PR 2, migration 1006): records custodian, account closure (S15), support grants (S17, F38), support console (metadata only).
- Built (support plan phase 2, migration 1011): support case diagnostics, `app.support_case_diagnostics()`, and the console case page.
- Built (migration 1012): support creates an account for a new person (`/v1/support/accounts`, web `/support/accounts` linked from the console): no password (set-password email `account.created`, link lasts 1 hour, sent after commit), no team (a team's owners invite), `invited` / `active`, send again (once a minute); 20 an hour per staff member counting refused attempts, under a per-staff lock; the login is deleted if anything after it fails. Core additions: `SupabaseAuth.admin_create_user/admin_delete_user/admin_recovery_token`, email template `account.created`.
- Built (PR 3, migration 1008): `POST /v1/support/custody-transfers` behind the flag `support_custody_transfer` **(legal check)**: support staff, to the expert's named records custodian (an open account, not support staff, not the recorder) or the case's own team organisation; served cases only; audited as `support.custody_transferred`. One holder at a time (409 `custody_already_transferred`, under a per-case transaction lock). Step-up is checked before the flag (core guard `test_foundation_step_up.py` scans every step-up route with no flag set). A refused attempt is audited as `support.custody_transfer_refused` (target `refused:<code>`) in a transaction of its own after the request's (`audit.record_detached`), so it stays though the request rolls back; requests turned away before the transfer is considered (step-up, the flag, not staff, a malformed body) are not audited here. A closed team organisation cannot receive custody (409 `organisation_closed`, through `app.support_organisation_closed()`, migration 1010). A served case gets one replacement: asking again for the same expert and consent returns the one already made with 200, and for anything else is 409 `replacement_exists` (foundation migration 0034).

## Cross-slice requests
- O → C: enforce `organisations.policy.required_template_id` (`core/contracts/org_policy.py`) when a case is created for a team.
- O → F: team cases invoice from the organisation's billing profile (already so in `app.case_billing_profile`); the report build uses the organisation's required template when set.
- O → H: cap a case's retention at `max_retention_years`; account closure deletes unserved personal cases through `request_case_deletion` (H's `delete_case`).
- O → A: copy a bundle into a replacement case only with the instructing party's recorded consent.

## Web (Phase 2) as built
- Routes: `/organisations/:orgId` (S27, screen `organisation_settings`), `/organisations/:orgId/leave` (`organisation_leave`), `/invites/:token` (outside the signed-in guard, sign-in layout classes), `/account/custodian` and `/account/close` (S28 sections as registered screens `account_custodian`, `account_close`), `/cases/:caseId/team` (change named expert, support grant card; screen `case_team`), `/support` (staff only; the server answers anyone else 404).
- Slots (foundation PR #66): S28 sections "If you can't carry on" and "Close your account" are registered with `registerAccountSection` (the `/account/custodian` and `/account/close` pages stay as full-page views); case header actions "Change named expert", "Avodeck support" and (served, flag) "Create a replacement case" are registered with `registerCaseAction`; the invite page uses `AuthLayout` from `foundation/ui`.
- Still open: a `billing_profile` screen key (F) for the bank-details link; the replacement-case route and the support custody-transfer route, which stay behind the flags `replacement_case` and `support_custody_transfer` (`VITE_FLAG_*=1`) until they exist.
- `GET /v1/organisations/{org_id}/members` gains `case_count` (cases the member is named expert on in that organisation; owners and admins only, null for others; migration 1009), `GET /v1/cases/{case_id}/named-expert/candidates` and `GET /v1/me/close` feed the web screens, and the organisation says its `allowed_actions` and `assignable_roles`, drawn as the board's "cases" column.
- Playwright against the live API: `web/e2e/organisations/` (`seed.py` arranges people and sessions; set `E2E_LIVE_ADMIN_DB` and `API_DEV_ORIGIN`; the file skips without them).

## Out of scope
SSO/SAML (deferred, `docs/decisions/deferred.md`), seat billing changes beyond Stripe subscription quantity, cross-cell organisations.

## Definition of done
Root checklist, plus the S1, S2, S3, S15 and S17 scenarios pass end to end on staging.
