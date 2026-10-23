"""The case permissions matrix from docs/spec/permissions.md, as one table.

`app.case_action_allowed()` (migration 0003, redefined in 0017, 0019, 0021 and 0200) mirrors this table, and
`app.case_role_allowed()` (migration 0030, redefined in 0032) adds the `support` and `custodian` roles to it;
the guard test `tests/guards/test_permissions.py` checks both against the document, cell by cell.

`ORG_ACTIONS` is the document's "Organisation actions" table, checked by `require_org_access`
(`core/auth/org_access.py`) and `tests/guards/test_foundation_org_access.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CASE_ROLES = ("named_expert", "co_expert", "supervisor", "assistant", "viewer")
ORG_ROLES = ("owner", "admin", "expert", "assistant", "viewer")
ORG_KINDS = ("personal", "team")
# Avodeck support under a live grant from the named expert (permissions.md "Avodeck staff"). Not a
# `case_members` role: `app.case_access()` gives it only to current staff while the grant lasts.
SUPPORT_ROLE = "support"
# A records custodian of a served case transferred to them by support (permissions.md, the "Records
# custodian" column; architecture 10.1 "Death or incapacity"). Not a `case_members` role either:
# `app.case_access()` gives it while a `custody_transfers` row that is not revoked names the person (or an
# organisation they are a current owner or admin of) and the case has been served (migration 0032).
CUSTODIAN_ROLE = "custodian"

# What an organisation owner or admin who is not on the case may do:
# none; admin = owner and admin; owner = owner only; team_admin = owner and admin, team organisations only.
OrgGrant = Literal["none", "admin", "owner", "team_admin"]

_ALL = frozenset(CASE_ROLES)
_HELPERS = frozenset({"named_expert", "assistant"})
_READERS = frozenset({"named_expert", "co_expert", "supervisor", "assistant"})
_NAMED = frozenset({"named_expert"})
_NOBODY: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ActionRule:
    case_roles: frozenset[str]
    org: OrgGrant = "none"
    step_up: bool = False


CASE_ACTIONS: dict[str, ActionRule] = {
    "case.view_metadata": ActionRule(_ALL, "admin"),
    "case.view_content": ActionRule(_ALL),
    "case.edit_setup": ActionRule(_HELPERS),
    "case.upload": ActionRule(_HELPERS),
    "case.manage_members": ActionRule(_NAMED, "admin"),
    "case.reassign_named_expert": ActionRule(_NOBODY, "admin", step_up=True),
    "case.hold": ActionRule(_HELPERS),
    "case.change_jurisdiction": ActionRule(_NAMED, step_up=True),
    "conflict.declare": ActionRule(_NAMED),
    "page.read": ActionRule(_READERS),
    "page.quarantine": ActionRule(_HELPERS),
    "page.replace": ActionRule(_HELPERS),
    "page.rotate": ActionRule(_HELPERS),
    "page.review_duplicate": ActionRule(_HELPERS),
    "page.transcribe": ActionRule(_HELPERS),
    "fact.verify": ActionRule(_NAMED),
    "link.suggest": ActionRule(_READERS),
    "link.decide": ActionRule(_NAMED),
    "finding.decide": ActionRule(_NAMED),
    "availability.override": ActionRule(_NAMED),
    "consultation.note": ActionRule(_NAMED),
    "question.confirm": ActionRule(_NAMED),
    "opinion.write": ActionRule(_NAMED),
    "comment.write": ActionRule(_READERS),
    "report.build": ActionRule(_HELPERS),
    "report.sign": ActionRule(_NAMED, step_up=True),
    "report.serve": ActionRule(_NAMED, step_up=True),
    "report.withdraw": ActionRule(_NAMED, step_up=True),
    "request.draft": ActionRule(_HELPERS),
    "request.send": ActionRule(_NAMED, step_up=True),
    "invoice.create": ActionRule(_HELPERS, "team_admin"),
    "invoice.send": ActionRule(_NAMED, "team_admin", step_up=True),
    "credit_note.issue": ActionRule(_NAMED, "team_admin", step_up=True),
    "invoice.record_payment": ActionRule(_HELPERS, "admin"),
    "case.export": ActionRule(_NAMED, "admin", step_up=True),
    "case.retention": ActionRule(_NAMED, "admin", step_up=True),
    "legal_hold.set": ActionRule(_NAMED, "owner", step_up=True),
    "legal_hold.release": ActionRule(_NAMED, "owner", step_up=True),
    "case.withdraw": ActionRule(_NAMED, step_up=True),
    "support_access.grant": ActionRule(_NAMED, step_up=True),
    "case.delete_now": ActionRule(_NAMED, "owner", step_up=True),
    "case.settle": ActionRule(_NAMED),
    "case.archive": ActionRule(_NAMED),
    "case.reopen": ActionRule(_NAMED),
    "case.view_audit": ActionRule(_NAMED, "admin"),
    "case.view_certificate": ActionRule(_NAMED, "admin"),
}


# All the support role may do (permissions.md, the "Avodeck support" column): look, never change.
# The same list is in `app.case_role_allowed()` (migration 0030).
SUPPORT_ACTIONS = frozenset({"case.view_metadata", "case.view_content", "case.view_audit"})
# All a records custodian may do: read, never change. The same list is in `app.case_role_allowed()` (0032).
CUSTODIAN_ACTIONS = frozenset({"case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"})


def _org_allows(grant: OrgGrant, org_role: str | None, org_kind: str | None) -> bool:
    if grant == "none" or org_role not in ("owner", "admin"):
        return False
    if grant == "owner":
        return org_role == "owner"
    if grant == "team_admin":
        return org_kind == "team"
    return True


def allowed(action: str, case_role: str | None, org_role: str | None, org_kind: str | None) -> bool:
    """May someone with this case role and organisation role do `action`? Unknown actions: never."""
    rule = CASE_ACTIONS.get(action)
    if rule is None:
        return False
    by_case = (
        case_role in rule.case_roles
        or (case_role == SUPPORT_ROLE and action in SUPPORT_ACTIONS)
        or (case_role == CUSTODIAN_ROLE and action in CUSTODIAN_ACTIONS)
    )
    return by_case or _org_allows(rule.org, org_role, org_kind)


def allowed_actions(case_role: str | None, org_role: str | None, org_kind: str | None) -> list[str]:
    return sorted(a for a in CASE_ACTIONS if allowed(a, case_role, org_role, org_kind))


# --- organisation actions -------------------------------------------------------------------------

_ORG_EVERYONE = frozenset(ORG_ROLES)
_ORG_CASE_CREATORS = frozenset({"owner", "admin", "expert", "assistant"})
_ORG_ADMINS = frozenset({"owner", "admin"})
_ORG_OWNER = frozenset({"owner"})


@dataclass(frozen=True)
class OrgActionRule:
    roles: frozenset[str]
    step_up: bool = False


ORG_ACTIONS: dict[str, OrgActionRule] = {
    "org.view_members": OrgActionRule(_ORG_EVERYONE),
    "org.manage_members": OrgActionRule(_ORG_ADMINS, step_up=True),
    "org.create_case": OrgActionRule(_ORG_CASE_CREATORS),
    "org.view_all_cases": OrgActionRule(_ORG_ADMINS),
    "org.set_policy": OrgActionRule(_ORG_ADMINS, step_up=True),
    "org.billing": OrgActionRule(_ORG_ADMINS, step_up=True),
    "org.close": OrgActionRule(_ORG_OWNER, step_up=True),
    "org.export": OrgActionRule(_ORG_OWNER, step_up=True),
}


def org_allowed(action: str, org_role: str | None) -> bool:
    """May a current member with this role do `action` for the organisation? Unknown actions: never."""
    rule = ORG_ACTIONS.get(action)
    return rule is not None and org_role in rule.roles


def org_allowed_actions(org_role: str | None) -> list[str]:
    return sorted(a for a in ORG_ACTIONS if org_allowed(a, org_role))
