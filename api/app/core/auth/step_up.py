"""Rule 10: step-up. Risky person actions depend on `require_step_up`; risky case actions get the
same check from `require_case_access` (🔒 in docs/spec/permissions.md). Both refuse with 403 unless MFA
was completed in the last 10 minutes; the web app then asks for a code (`POST /v1/auth/mfa/verify`), or
first sets up the authenticator (`POST /v1/auth/mfa/enroll`), and repeats the request.

Sign-in is password only (ADR 0025), so a risky action is where a session first meets the code. Every
refusal carries `mfa_enrolled` so the web app knows which to show:
- no code yet this session: `mfa_required` (`mfa_enrolled` false: set it up first; true: enter a code);
- a code this session, more than 10 minutes ago: `step_up_required`."""

from __future__ import annotations

from fastapi import Request

from app.core.auth.session import Session, current_session
from app.core.errors import ApiError


def step_up_required() -> ApiError:
    return ApiError("step_up_required", "Enter a code from your authenticator app to continue.", 403, mfa_enrolled=True)


def mfa_required(session: Session) -> ApiError:
    """No code yet this session: set up the authenticator first, or enter a code from it."""
    if not session.mfa_enrolled:
        return ApiError("mfa_required", "Set up your authenticator app to continue.", 403, mfa_enrolled=False)
    return ApiError("mfa_required", "Enter a code from your authenticator app to continue.", 403, mfa_enrolled=True)


def check_session_mfa(session: Session) -> None:
    """A code at some point this session (support routes, ADRs 0020 and 0023); no 10-minute window."""
    if not session.mfa_complete or session.mfa_verified_at is None:
        raise mfa_required(session)


def check_step_up(session: Session) -> None:
    if session.stepped_up:
        return
    check_session_mfa(session)
    raise step_up_required()


class _StepUp:
    """A FastAPI dependency; `step_up` lets the route scanner (tests/guards) find these routes."""

    step_up = True

    async def __call__(self, request: Request) -> None:
        check_step_up(current_session(request))


require_step_up = _StepUp()
