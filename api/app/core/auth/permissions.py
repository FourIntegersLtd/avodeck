"""The case permissions matrix from docs/spec/permissions.md, as one table.

`app.case_action_allowed()` (migration 0003, redefined in 0017, 0019, 0021 and 0200) mirrors this table; the guard test
`tests/guards/test_permissions.py` checks both against the document, cell by cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CASE_ROLES = ("named_expert", "co_expert", "supervisor", "assistant", "viewer")
ORG_ROLES = ("owner", "admin", "expert", "assistant", "viewer")
ORG_KINDS = ("personal", "team")

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
    return (case_role in rule.case_roles) or _org_allows(rule.org, org_role, org_kind)


def allowed_actions(case_role: str | None, org_role: str | None, org_kind: str | None) -> list[str]:
    return sorted(a for a in CASE_ACTIONS if allowed(a, case_role, org_role, org_kind))
