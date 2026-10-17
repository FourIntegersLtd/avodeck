"""Rule 10: step-up. Risky person actions depend on `require_step_up`; risky case actions get the
same check from `require_case_access` (🔒 in docs/spec/permissions.md). Both refuse with 403
`step_up_required` unless MFA was completed in the last 10 minutes; the web app then asks for a
code (`POST /v1/auth/mfa/verify`) and repeats the request."""

from __future__ import annotations

from fastapi import Request

from app.core.auth.session import Session, current_session
from app.core.errors import ApiError


def step_up_required() -> ApiError:
    return ApiError("step_up_required", "Enter a code from your authenticator app to continue.", 403)


def check_step_up(session: Session) -> None:
    if not session.stepped_up:
        raise step_up_required()


class _StepUp:
    """A FastAPI dependency; `step_up` lets the route scanner (tests/guards) find these routes."""

    step_up = True

    async def __call__(self, request: Request) -> None:
        check_step_up(current_session(request))


require_step_up = _StepUp()
