# Threat model

Method: STRIDE over the data flows in architecture section 2. Review this file whenever a slice adds a new data flow, external service or sharing feature. The highest risks are marked ●.

## What we protect

1. Patient records and everything derived from them: page images, OCR text, facts, transcripts.
2. The expert's opinion and report versions (integrity matters as much as confidentiality, since they are evidence).
3. The audit, page-read and verification logs (they prove the expert's process).
4. Accounts and sessions.

## Trust boundaries

Browser ↔ FastAPI (internet) · FastAPI/worker ↔ Supabase (London) · worker ↔ Bedrock London and Azure UK South · FastAPI ↔ Resend, Stripe, Sentry (no patient data) · spot-check colleagues and e-sign counterparts (external people) · the expert's own device for the offline package.

## Threats and mitigations

| # | Threat | STRIDE | Mitigation | Test or check |
|---|---|---|---|---|
| T1 ● | Stolen session cookie or credential stuffing | Spoofing | httpOnly, Secure, SameSite=Strict cookie; mandatory MFA; captcha and rate limits on sign-in; leaked-password check; 30-min idle timeout; sign-in alerts | Core auth tests |
| T2 ● | Expert A reads expert B's case (broken access control) | Info disclosure | `require_case_access(action)` on every route (permissions matrix tested cell by cell, including a person in two organisations), RLS as second layer, route-table scan test | Core ownership test (every route) |
| T3 ● | Malicious PDF exploits the parser (or a zip bomb) | Elevation | ClamAV; qpdf in a sandboxed subprocess with CPU, memory and time limits; page and size caps (C1); worker has no outbound network except allow-listed APIs | Fuzz test with malformed PDFs |
| T4 ● | Prompt injection in a scanned page makes the AI output false facts or leak data | Tampering | Structured output only; values must appear in cited words (provenance); AI has no tools or network; AI never sees other cases; expert verifies every fact | Injection fixtures in eval set |
| T5 | Patient data leaks into logs, Sentry or analytics | Info disclosure | Rule 6; Sentry scrubbing; PostHog event allow-list with no free text; CI and weekly log scans with canary identifiers | Core log test |
| T6 | Data processed outside the UK | Info disclosure | Cell allow-list; IAM deny on `global.*`/`eu.*`; fail closed (F24) | Core region test |
| T7 | Served report altered after the event | Tampering | Immutable rows (DB trigger), SHA-256 hashes, hash-chained audit, optional RFC 3161 timestamp | Core locked-report test |
| T8 | Expert disputes that they read a page | Repudiation | Append-only, hash-chained `page_reads` with dwell time; exportable | Hash chain test |
| T9 | Spot-check or e-sign link forwarded or guessed | Info disclosure | Random 128-bit tokens, stored hashed; 7-day expiry; emailed code; watermarked crops only, never full pages; access logged | F slice tests |
| T10 | Offline package lost with a laptop | Info disclosure | Sealed with AES-GCM; the key is released once and at once wrapped on the device by a key made from a PIN the expert sets at download (PBKDF2-SHA256, 600,000 rounds, random 16-byte salt); only the wrapped key and salt are kept, so nothing on the device opens without the PIN, and 5 wrong PINs wipe the copy. Auto-expiry; when online, the witness box asks `GET …/offline-packages/{pkg_id}/status` (status only, no case content) and wipes on revoked, expired or 404, keeping the copy on a network failure. Minimum content. Open: a close does not yet revoke packages (H) | I slice tests |
| T11 | Insider or compromised Supabase service key reads data | Info disclosure | Per-case envelope encryption of page text and images (ADR 0010); service key only in App Platform secrets; least-privilege keys per component | Key rotation drill |
| T12 | CSRF on state-changing routes | Tampering | Double-submit token, origin check, SameSite=Strict | Core CSRF test |
| T13 | XSS through OCR text rendered in the reader | Tampering | Text rendered as text (React escaping), strict CSP, no `dangerouslySetInnerHTML` lint rule | CSP and lint checks |
| T14 | Supply-chain attack via a dependency | Elevation | Lockfiles, Dependabot/Renovate, SBOM, licence and vulnerability scan in CI, pinned base images | CI |
| T15 | Denial of service with huge uploads or many jobs | Denial of service | Per-expert quotas (C1–C4), queue limits, App Platform rate limiting | Load test |
| T16 | Email spoofing pretending to be Avodeck | Spoofing | SPF, DKIM, DMARC on the sending domain; no links to patient data in emails | DNS check |
| T17 | Backup restored to the wrong place, or kept beyond deletion | Info disclosure | Backups stay in Supabase London; certificate states the backup expiry; crypto-shredding makes old backups unreadable | Restore drill |
| T18 | Organisation owner or admin reads patient content on a case they aren't on | Info disclosure | Admins get metadata only; content needs a case role; matrix tested cell by cell | `test_permissions.py` |
| T19 | Former member keeps access after leaving | Elevation of privilege | Membership end revokes all access except named cases; sessions re-checked per request | S2 scenario test |
| T20 | Invite link forwarded to someone else | Spoofing | Single use, 7-day expiry, must be accepted by the invited email, step-up to send. The invite mail (`org.invite`) carries only the organisation's id, a role code, the `/invites/<token>` link and the expiry date: no names, nothing to phish with | Invite tests; `test_foundation_email_org_templates.py` |
| T21 | Support staff browse case content | Info disclosure | No content access without the named expert's 24-hour grant; every view audited and shown to the expert. Staff are a hand-kept list (`support_staff`, `docs/ops/support-staff.md`) no route writes; the `support` role needs current staff and a live grant from the case's named expert (expiry checked on every read, revocation final); it only reads; every support call and console route needs MFA in the last 10 minutes; RLS repeats it | S17 scenario test; `test_foundation_support_access.py` |
| T24 | A support staff account is taken over, or someone leaves support but keeps access | Elevation of privilege | MFA on every staff account and fresh MFA (10 minutes) on every console and support call; removing a person from `support_staff` ends every grant's effect at once; grants are for one case and at most 24 hours | `test_foundation_support_access.py` |
| T25 | Custody transfer or reassignment hands a case to the wrong person | Elevation of privilege | Reassignment only by an owner/admin with step-up and only before serving; both experts told (`case.reassigned`, ids only); the new expert starts with no reads or verifications. Custody transfer (death or incapacity) is done by support only with evidence, read-only, and recorded in the audit trail (legal check) | S3 scenario test (O) |
| T26 | A closed account keeps acting on its cases | Elevation of privilege | A closed account reads only the served cases it is the named expert of; every other action, case and organisation action is 404, in the API and in RLS | `test_foundation_closed_account_access.py` |
| T22 | Restored backup brings a deleted case back | Info disclosure | Deletion ledger replayed before reopening; destroyed case keys make the content unreadable anyway | Restore drill (S14) |
| T23 | Wrong patient's pages used or disclosed | Info disclosure | Identifier mismatch suggests quarantine; quarantined pages excluded everywhere and deleted after notice | S5 scenario test |

## Residual risks we accept (and say so to customers)

- Supabase and DigitalOcean staff with platform access (mitigated by contracts, encryption and ADR 0010).
- Model providers process de-identified text (Bedrock and Azure terms: no training, no retention with logging off).
- The expert's own device security (MFA reduces, does not remove).
