"""ADR 0025: sign-in is password only; the authenticator is asked for at risky actions (rule 10).

A session after the password alone is `aal1`. Every gate that needs MFA answers 403 with a code the
web app acts on, and `mfa_enrolled` so it knows whether to set the authenticator up or ask for a code:

- not set up yet: `mfa_required`, `mfa_enrolled` false (the web app sets it up, then carries on);
- set up, no code this session: `mfa_required`, `mfa_enrolled` true;
- a code this session but more than 10 minutes ago: `step_up_required` (risky actions only).

Support lookups and changes need a code at some point this session (ADR 0020 as amended, ADR 0023).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.auth.session import Session
from app.core.auth.step_up import check_step_up
from app.core.auth.support import check_session_mfa
from app.core.errors import ApiError


def _session(*, aal: str, enrolled: bool, mfa_age: timedelta | None) -> Session:
    now = datetime.now(UTC)
    return Session(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        email="someone@example.test",
        full_name=None,
        csrf_hash="0" * 64,
        aal=aal,
        mfa_enrolled=enrolled,
        mfa_verified_at=None if mfa_age is None else now - mfa_age,
        last_seen_at=now,
        auth_access_token=None,
        auth_refresh_token=None,
        auth_expires_at=None,
    )


# Built when each test runs, not at import: the full suite runs for longer than the 10-minute window.
def NOT_SET_UP() -> Session:
    return _session(aal="aal1", enrolled=False, mfa_age=None)


def NO_CODE_YET() -> Session:
    return _session(aal="aal1", enrolled=True, mfa_age=None)


def CODE_11_MINUTES_AGO() -> Session:
    return _session(aal="aal2", enrolled=True, mfa_age=timedelta(minutes=11))


def CODE_JUST_NOW() -> Session:
    return _session(aal="aal2", enrolled=True, mfa_age=timedelta(minutes=1))


def _refusal(check: object, session: Session) -> tuple[int, str, object]:
    with pytest.raises(ApiError) as err:
        check(session)  # type: ignore[operator]
    return err.value.status, err.value.code, err.value.extra.get("mfa_enrolled")


def test_a_risky_action_from_a_password_only_session_asks_to_set_up_the_authenticator() -> None:
    assert _refusal(check_step_up, NOT_SET_UP()) == (403, "mfa_required", False)


def test_a_risky_action_from_a_set_up_session_with_no_code_yet_asks_for_a_code() -> None:
    assert _refusal(check_step_up, NO_CODE_YET()) == (403, "mfa_required", True)


def test_a_risky_action_after_a_code_more_than_10_minutes_ago_asks_again() -> None:
    assert _refusal(check_step_up, CODE_11_MINUTES_AGO()) == (403, "step_up_required", True)


def test_a_risky_action_right_after_a_code_goes_ahead() -> None:
    check_step_up(CODE_JUST_NOW())


def test_support_needs_a_code_this_session_and_says_whether_to_set_it_up() -> None:
    assert _refusal(check_session_mfa, NOT_SET_UP()) == (403, "mfa_required", False)
    assert _refusal(check_session_mfa, NO_CODE_YET()) == (403, "mfa_required", True)
    check_session_mfa(CODE_11_MINUTES_AGO())  # no fresh code (ADR 0023)
