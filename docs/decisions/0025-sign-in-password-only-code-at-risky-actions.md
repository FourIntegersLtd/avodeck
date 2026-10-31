# 0025 Sign-in is password only; the authenticator is asked for at risky actions

Date: 2026-10-25 · Status: accepted · Decided by: the founder · Amends: ADR 0020, 0023 (where support MFA is met)

**Problem.** Every sign-in asked for a 6-digit code from the authenticator app, and a new expert had to set the app up before seeing anything. For experts who are not confident with computers this was the hardest step of the first visit, and it came before any work that needed it. Rule 10 already asks for a code in the last 10 minutes before the actions that matter (sign, send or serve, export, delete and retention changes), so the code at sign-in added a step without guarding those actions any better.

**Decision.**
- After a correct password the session is `aal1` (`POST /v1/auth/sign-in` answers `status: "signed_in"`, with `mfa_enrolled`) and may use every ordinary signed-in route. `core/http.py` `SessionRoute` no longer refuses an `aal1` session. `mfa_required` stays in `SignInResult.status` so older clients keep working.
- Rule 10 is unchanged: `require_step_up` (and `require_case_access` with step-up, and the `support` case role) still needs `aal2` with a code in the last 10 minutes. The refusal now tells the two first-time cases apart (`core/auth/step_up.py` `check_step_up`):
  - no authenticator yet: 403 `mfa_required`, `mfa_enrolled: false` (set it up first);
  - an authenticator but no code this session: 403 `mfa_required`, `mfa_enrolled: true`;
  - a code this session but more than 10 minutes ago: 403 `step_up_required`, `mfa_enrolled: true`.
- `POST /v1/auth/mfa/enroll` and `/verify` work from an `aal1` session; verify finishes at `aal2` with `mfa_verified_at` = now, and the first verify still issues the ten recovery codes, shown once. `POST /v1/auth/mfa/recover` signs in at `aal1` with the authenticator removed, so the next risky action sets up the new phone.
- The web app asks at the action, not at sign-in: `foundation/platform-client` treats both codes as "a code is needed" and `foundation/lib/stepUp.tsx` either asks for the code or sets the authenticator up (picture, key, code, the backup codes once), then sends the action again. Cancelling says nothing was changed.
- Every expert still sets up an authenticator; they meet it at their first risky action.
- Support (ADR 0020, 0023): `require_support_staff` and `require_support_lookup` still need MFA completed in the session (`check_session_mfa`), now met through the same prompt the first time a support call answers `mfa_required`, rather than at sign-in. No support route asks for a fresh code.
- Dictation (`/v1/cases/{id}/dictation`) no longer refuses a password-only session; it is not a risky action.

**Supabase setting.** The cloud project's Auth setting "Limit duration of AAL1 sessions" (ends a session not MFA-verified within 15 minutes) must stay **off**: ordinary sessions now live at `aal1` for their whole life (30 minutes idle, 12 hours at most). `docs/runbooks/supabase-auth.md` records it.

**Consequences.**
- A stolen password alone now opens ordinary routes: reading cases and pages, writing drafts. It cannot sign, serve, export, delete, change retention, run a support lookup or act under a support grant. Captcha and rate limits on sign-in, the leaked-password check, sign-in alerts and the 30-minute idle timeout still apply (threat model T1).
- Accounts that support created (ADR 0024) still change the temporary password first; they become `active` when the authenticator is set up, which now happens at the first risky action.
- Revisit when an organisation asks for MFA at every sign-in; that would be an organisation policy, not a return to the old default.

**Tests.** `api/tests/unit/test_foundation_mfa_at_step_up.py`, `api/tests/http/test_foundation_auth_routes.py`, `api/tests/guards/test_foundation_step_up.py`, `api/tests/guards/test_foundation_support_access.py`, `web/src/foundation/lib/stepUpSetup.test.tsx`, `web/src/app/auth/SignIn.test.tsx`, `web/e2e/foundation/sign-in.spec.ts`.
