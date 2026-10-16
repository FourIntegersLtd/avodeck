# Permissions

The single source of truth for who may do what. `core/auth/require_case_access(action)` implements this table and `api/tests/guards/test_permissions.py` tests every cell. Row-level security mirrors it. If a new endpoint needs an action not listed here, add the row first (foundation approval).

## Two kinds of role

- **Organisation role** (`memberships.role`): what someone can do for an organisation as a whole.
- **Case role** (`case_members.case_role`, or being the case's `named_expert_id`): what someone can do on one case.

A person's rights on a case are the **union** of their case role and the administrative rights of their organisation role. Seeing patient content always needs a case role (named expert or case member); organisation owners and admins see case metadata only unless also added to the case.

## Case actions

✓ = allowed · — = not allowed (404 if they can't see the case at all, 403 if they can see it but not do this)

| Action | Named expert | Co-expert | Supervisor | Assistant | Viewer | Org owner/admin (not on case) |
|---|---|---|---|---|---|---|
| `case.view_metadata` (label, dates, status) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `case.view_content` (pages, facts, opinions) | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `case.edit_setup` (parties, instructing parties, links, hearings, directions) | ✓ | — | — | ✓ | — | — |
| `case.upload` (records, instructions, exhibits) | ✓ | — | — | ✓ | — | — |
| `case.manage_members` | ✓ | — | — | — | — | ✓ |
| `case.reassign_named_expert` (before serving) 🔒 | — | — | — | — | — | ✓ |
| `page.read` (counts towards citing) | ✓ | own reads only | own reads only | own reads only | — | — |
| `page.quarantine`, `page.replace`, `page.rotate` | ✓ | — | — | ✓ (suggest only; named expert confirms quarantine) | — | — |
| `fact.verify` | ✓ | — | — | — | — | — |
| `question.confirm` | ✓ | — | — | — | — | — |
| `opinion.write` | ✓ | — | — | — | — | — |
| `comment.write` | ✓ | ✓ | ✓ | ✓ | — | — |
| `report.build` | ✓ | — | — | ✓ | — | — |
| `report.sign` 🔒, `report.serve` 🔒, `report.withdraw` 🔒 | ✓ | — | — | — | — | — |
| `request.draft` (instructing-party emails) | ✓ | — | — | ✓ | — | — |
| `request.send` 🔒 | ✓ | — | — | — | — | — |
| `invoice.create` (draft) | ✓ | — | — | ✓ (draft only) | — | ✓ (team cases) |
| `invoice.send` 🔒, `credit_note.issue` 🔒 | ✓ | — | — | — | — | ✓ (team cases) |
| `invoice.record_payment` | ✓ | — | — | ✓ | — | ✓ |
| `case.export` 🔒 | ✓ | — | — | — | — | ✓ (administrative copy: report versions, invoices, logs; no page images) |
| `case.retention` 🔒 | ✓ (within org policy) | — | — | — | — | ✓ |
| `legal_hold.set` 🔒, `legal_hold.release` 🔒 | ✓ | — | — | — | — | owner only |
| `case.withdraw` 🔒 | ✓ | — | — | — | — | — |
| `support_access.grant` 🔒 | ✓ | — | — | — | — | — |
| `case.delete_now` 🔒 | ✓ (unserved only) | — | — | — | — | owner only (unserved only) |

After the named expert leaves the organisation, they keep every named-expert action on their named cases; the organisation keeps its "not on case" column.

## Organisation actions

| Action | Owner | Admin | Expert | Assistant | Viewer |
|---|---|---|---|---|---|
| View members | ✓ | ✓ | ✓ | ✓ | ✓ |
| Invite, change role, remove 🔒 | ✓ | ✓ (not owners) | — | — | — |
| Create a case for the organisation | ✓ | ✓ | ✓ | ✓ (named expert must be an expert member) | — |
| See all the organisation's cases (metadata) | ✓ | ✓ | own and assigned | assigned | assigned |
| Set policy 🔒, billing profile 🔒 | ✓ | ✓ | — | — | — |
| Close the organisation 🔒 | ✓ | — | — | — | — |
| Organisation-wide export of administrative data 🔒 | ✓ | — | — | — | — |

## Person actions (no case)

Only the person themselves: profile, expert profile, signature, declarations, sessions, recovery codes, email change 🔒, notification preferences, custodian 🔒, account closure 🔒. Organisation admins cannot reset another person's MFA or password.

## Avodeck staff

No case content. Metadata through the support console. Content only under an active `support_access_grants` row for that case, and every view is in the case audit trail.

## Tests

- Every cell above has a test using the fixture packs and two organisations, so a pass cannot depend on gb-eaw words.
- A "second case" test: the same person as named expert on a personal case and as assistant on a team case sees the right rights on each.
- A "left the organisation" test: named-expert rights survive, assistant rights don't.
