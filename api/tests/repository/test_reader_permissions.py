"""Slice B's case actions (docs/spec/permissions.md): who may type out a page and who may check facts.

`page.transcribe` (typing out a page, a second reading, relevance, asking for a clearer copy) is for the
named expert and the assistant; `fact.verify` (checking, flagging and moving facts) for the named expert
only. The code and the database matrix agree.
"""

from __future__ import annotations

import psycopg
import pytest

from app.core.auth.permissions import CASE_ACTIONS, allowed

READER_ACTIONS = {
    "page.read": {"named_expert", "co_expert", "supervisor", "assistant"},
    "page.transcribe": {"named_expert", "assistant"},
    "fact.verify": {"named_expert"},
}
ROLES = ("named_expert", "co_expert", "supervisor", "assistant", "viewer")


@pytest.mark.parametrize("action", sorted(READER_ACTIONS))
def test_reader_actions_are_known_and_need_no_step_up(action: str) -> None:
    assert action in CASE_ACTIONS
    assert CASE_ACTIONS[action].step_up is False


@pytest.mark.parametrize(("action", "role"), [(a, r) for a in sorted(READER_ACTIONS) for r in ROLES])
def test_only_the_documented_case_roles_may_act(admin: psycopg.Connection, action: str, role: str) -> None:
    expected = role in READER_ACTIONS[action]
    assert allowed(action, role, "owner", "team") is expected
    row = admin.execute("select app.case_action_allowed(%s, %s, 'owner', 'team')", (action, role)).fetchone()
    assert row == (expected,)
