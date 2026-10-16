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
- `POST /v1/cases/{id}/support-access` 🔒; `DELETE /v1/cases/{id}/support-access/{grant_id}`
- `POST /webhooks/stripe`: Avodeck's own subscription (seats per organisation), signature-verified; moved here from the foundation

## Tables owned
`invites`, `billing_profiles` (team ones; personal ones created by F), `support_access_grants`. Writes team rows in `organisations` and `memberships` (tables owned by foundation), `organisations.policy`, `people.records_custodian`, `people.closed_at`.

## Contracts
- **In:** foundation `people`, `organisations`, `memberships`, `case_members`, `require_case_access`, notifications.
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

## Out of scope
SSO/SAML (deferred, `docs/decisions/deferred.md`), seat billing changes beyond Stripe subscription quantity, cross-cell organisations.

## Definition of done
Root checklist, plus the S1, S2, S3, S15 and S17 scenarios pass end to end on staging.
