# 0020 Support lookups need MFA at sign-in, not every 10 minutes

Date: 2026-10-24 · Status: accepted, partly superseded by ADR 0023 (no support action needs a fresh MFA code) · Decided by: the founder

**Problem.** Every support console call needed MFA completed in the last 10 minutes (`aal2` step-up, `check_step_up` in `core/auth/support.py`). Support staff were asked for a new code every 10 minutes just to search, though a search shows metadata only.

**Decision.**
- **Read-only support lookups** (`GET /v1/support/...`) use `require_support_lookup`: current support staff, in a session whose MFA was completed at sign-in (`aal2`, `mfa_verified_at` set; otherwise 403 `mfa_required`). The session's 30-minute idle limit and 12-hour maximum still apply. They do not need the 10-minute window.
- **Support actions that change something** (`POST`, `PUT`, `PATCH`, `DELETE` under `/v1/support`: custody transfers, content-access use, and the coming resume, requeue, MFA unlock, invite extension and offline-pack revocation) keep `require_support_staff` and the 10-minute step-up exactly as before.
- Rule 10 is unchanged: sign, send, export, delete and retention changes still need step-up. Case routes reached under the `support` case role (`require_case_access` under a grant) still need MFA in the last 10 minutes.

**Safeguards.**
- Staff only: someone not in `app.support_staff` (or removed) gets 404, as if the console did not exist.
- Metadata only: the console never shows case content, names or page text; content needs a live grant from the named expert.
- Every lookup is audited (`support.case_looked_up` on the case's audit trail).
- MFA completed for the session at sign-in; idle 30 minutes and 12 hours at most, then sign in again.
- `api/tests/guards/test_foundation_support_access.py` scans the route table: every `GET` support route uses the lookup gate, and every other support route needs the 10-minute step-up. (Superseded by ADR 0023: no support route asks for step-up.)
