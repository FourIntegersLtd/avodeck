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
| `case.settle`, `case.archive`, `case.reopen` | ✓ | — | — | — | — | — |
| `case.view_audit`, `case.view_certificate` | ✓ | — | — | — | — | ✓ |
| `support_access.grant` 🔒 | ✓ | — | — | — | — | — |
| `case.delete_now` 🔒 | ✓ (unserved only) | — | — | — | — | owner only (unserved only) |

After the named expert leaves the organisation, they keep every named-expert action on their named cases; the organisation keeps its "not on case" column.

## Checks the database repeats (rule 9, ADR 0013)

Row-level security lets a person update a row; triggers then check each change against the action it is, for the person `app.person_id` names:
- `report_versions.status`: → locked needs `report.sign`, → served `report.serve`, → withdrawn `report.withdraw`; any other edit `report.build`.
- `cases.named_expert_id`: `case.reassign_named_expert`, and only while the case has never been served (no served or withdrawn report version, status not served or settled).
- `cases.retention_policy`, `delete_after`, `on_hold`: `case.retention`.
- `cases.status`: → deleted `case.delete_now` (never-served cases only), → withdrawn `case.withdraw`, → served `report.serve`, → settled `case.settle`, → archived `case.archive`, settled or archived → active `case.reopen`; any other move and every other column `case.edit_setup`.
- `app.purge_case()`: only the worker, inside the case's own job (`app.job_case_id`); refused while the case is on hold or under an active legal hold.
- `app.enqueue_job()`: the action comes from `job_kind_actions` by job kind (unknown kinds need `case.upload`); callers cannot pass one.

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

Only the person themselves: profile, expert profile, signature, declarations, sessions, recovery codes, email change 🔒, notification preferences, custodian 🔒, account closure 🔒. Organisation admins cannot reset another person's MFA or password: no route takes another person's id, and recovery needs the person's own password as well as a recovery code.

## Avodeck staff

No case content. Metadata through the support console. Content only under an active `support_access_grants` row for that case, and every view is in the case audit trail.

## Tests

- Every cell above has a test using the fixture packs and two organisations, so a pass cannot depend on gb-eaw words.
- A "second case" test: the same person as named expert on a personal case and as assistant on a team case sees the right rights on each.
- A "left the organisation" test: named-expert rights survive, assistant rights don't.
