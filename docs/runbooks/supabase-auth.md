# Supabase Auth settings that must not change

FastAPI is the only caller of Supabase Auth (rule 4). These dashboard settings (Authentication > Settings) in each cell's project are part of how sign-in works; changing one breaks sign-in or a safety rule.

| Setting | Value | Why |
|---|---|---|
| Multi-factor: TOTP | enabled | Rule 10: risky actions need a code from the authenticator app in the last 10 minutes |
| Limit duration of AAL1 sessions | **off** | Sign-in is password only (ADR 0025): ordinary sessions stay `aal1` until the first risky action. On, it would end every session not MFA-verified within 15 minutes |

Check both after any project is created or restored, and before a release that touches `api/app/core/auth/`.
