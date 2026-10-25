# 0023 Support actions need no fresh MFA code

Date: 2026-10-25 · Status: accepted · Decided by: the founder · Supersedes: the 10-minute step-up part of ADR 0020

**Problem.** ADR 0020 let support lookups run on the MFA done at sign-in but kept the 10-minute step-up (`aal2` in the last 10 minutes) on every support route that changes something: custody transfers, account creation, new temporary passwords, job resume and requeue. During early testing the founder is the only support user and is asked for a fresh code on almost every action.

**Decision.** Founder decision 2026-10-25: support use by the founder during early testing; staff-only access and audit remain.
- No `/v1/support/...` route asks for a fresh MFA code. `require_support_staff` and `require_support_lookup` both need current support staff in a session whose MFA was completed at sign-in (403 `mfa_required` otherwise), within the 30-minute idle and 12-hour limits.
- Everything else stays: anyone not current staff gets 404; changes need the CSRF token, and account changes an `Idempotency-Key`; every action is audited; each job action and custody transfers stay behind their feature flags.
- Rule 10 (AGENTS.md) is unchanged: sign, send, export, delete and retention changes still need step-up, and so do case routes reached under the `support` case role (a grant from the named expert), which still need MFA in the last 10 minutes on every call.

**Consequences.** A stolen, still-live staff session can act as support until it idles out (30 minutes) or reaches 12 hours, without a new code. Accepted for early testing. Revisit before a second person joins support: the trigger is any new row in `app.support_staff`.

**Tests.** `api/tests/guards/test_foundation_support_access.py`: every support route uses the staff gate, none asks for step-up, and a session whose MFA was 30 minutes ago can search, look and record a custody transfer.
