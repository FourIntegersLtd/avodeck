# Failure modes

What happens when things go wrong, so the code handles each case on purpose. Principle: **never lose expert work, never guess, always tell the expert in plain words what happened and what to do.**

| # | Failure | Detection | Automatic handling | What the expert sees | Owner |
|---|---|---|---|---|---|
| F1 | Upload drops part-way | Missing chunks at `complete` | Resume from the last good chunk | "Your upload paused. It will continue when your connection is back." | Foundation |
| F2 | Uploaded file is not a valid PDF, or is encrypted | Split step fails | Try qpdf repair; if encrypted, stop that document | "We couldn't open *filename*. It may be password-protected. Ask the {instructing_party} for an unlocked copy." Pre-filled 08b request | A |
| F3 | Virus scan flags a file | ClamAV | Quarantine; never processed | "This file was blocked for safety." Audit entry | Foundation |
| F4 | OCR fails on one page | Azure error after 3 tries | Mark page `unreadable`; continue with the rest | Page appears in "Pages we couldn't read" | A |
| F5 | Azure or Bedrock outage or throttling | 429/5xx | Exponential back-off; job pauses up to 2 hours, then `failed` with a resume button | Progress shows "Waiting for the reading service, your bundle is safe" | A |
| F6 | Worker crashes mid-job | Job heartbeat stops | Job re-queued; idempotent steps skip finished pages | Progress continues | Foundation |
| F7 | Model returns invalid JSON or wrong schema | Pydantic validation | One retry; then that page's extraction is marked `needs_human` | Page still readable; "No suggestions for this page" | A |
| F8 | Model cites words that don't contain the value | `provenance.check` | Reject value; one retry; then drop the suggestion | Nothing wrong shown; dropped count logged | A |
| F9 | Prompt injection text in a scanned document ("ignore instructions…") | Output schema plus provenance check (values must be in cited words) | Injected instructions cannot add fields or free text; suspicious pages flagged in metrics | Nothing | A, core |
| F10 | Identifier stripping misses something | Weekly synthetic canary test; log scanner | Block release if the canary fails | Nothing | Core |
| F11 | Cost guard tripped (K3) | Running cost per case | Job paused; alert to you | "Taking longer than usual, we're checking it." | Foundation |
| F12 | Date ambiguous (03/04 with no context) | Date normaliser | Not guessed; marked "Please check" | Badge on the fact | A |
| F13 | Duplicate wrongly suggested | Expert undo | Undo restores the page | — | A, B |
| F14 | Expert rejects a fact already cited | State machine guard | Warn and list citing paragraphs | "This fact is used in your opinion on Q2. Change it?" | B, E |
| F15 | Two tabs edit the same opinion | `If-Match` version check | 412 `stale` with both versions | "You changed this in another window. Which version do you want to keep?" | E |
| F16 | Report build fails (template problem) | Build job error | Fall back to the standard Avodeck template, flag the template issue | "We couldn't use your template this time; your report is in the standard layout." | F |
| F17 | Session expires while writing | 401 | Autosave every 5 s; draft kept server-side | Sign in again and continue where you were | Foundation, E |
| F18 | Email delivery fails (Resend) | Webhook | Retry; then in-app notice | Today item still appears | Foundation |
| F19 | E-sign webhook never arrives | Timeout check daily | Poll provider; alert after 48 h | "Waiting for Dr X to sign" with a reminder button | I |
| F20 | Deletion job interrupted | Job status | Resumes; certificate only issued when everything is gone | "Deletion in progress" | H |
| F21 | Backup restore needed | Incident | Point-in-time restore runbook (`docs/ops/restore.md`) | Status message | You |
| F22 | Pack file invalid | CI pack-schema check; loader refuses at start-up | Deployment blocked; running cells keep the last good pack | Nothing | Foundation |
| F23 | Model or prompt change lowers accuracy | Nightly eval | Change blocked from release | Nothing | All AI slices |
| F24 | Region model becomes unavailable | Bedrock error | Fail closed (never fall back to another region) | Processing paused message; you choose an in-region replacement | Core |
| F25 | Wrong person's record in the bundle | Pack identifier recognisers disagree with the case subject; expert report | Suggest quarantine; never auto-hide | "Pages 412–415 may belong to someone else. Check and remove them?" | A |
| F26 | Stale write (someone else saved first) | `If-Match` ETag mismatch | 412; nothing overwritten | "This changed in another window. Which version do you want to keep?" | Foundation, all |
| F27 | Repeated request (double click, retry after timeout) | Same `Idempotency-Key` | Return the first result | Nothing (one invoice, one serving) | Foundation |
| F28 | Scheduled job missed (deploy, outage) | Run key for that date absent | Next run catches up each missed date once | Reminders arrive late, never twice | Foundation |
| F29 | Backup restored after deletions | Restore runbook step | Replay `deletion_ledger` before reopening (`select app.replay_deletion_ledger()`, operators only: removes each case again and destroys its key again) | Nothing | Foundation (ledger, replay); H (runbook) |
| F30 | Deletion due while a legal hold is active | Hold check in deletion job | Skip the case; notify | "Deletion paused: legal hold until …" | H |
| F31 | Named expert leaves the organisation | Membership ended | Keep named-case access; remove all other access | Their Today shows only their named cases from that organisation | O |
| F32 | Last owner tries to leave | Membership guard | 409 | "Make someone else an owner first." | O |
| F33 | Email bounces | Resend webhook | Mark address; stop emails; in-app banner | "We couldn't email you. Check your address." | Foundation |
| F34 | Pack correction marked mandatory | Pack version change | Block signing on unserved cases until acknowledged | "The court wording has been corrected. Please review before signing." | Foundation, F |
| F35 | Court bundle re-issued after the trial pack was built | New court bundle version | Mark trial pack and offline packages stale | "New court bundle received; rebuild your trial pack." | I |
| F36 | Non-PDF file uploaded (email, image, Word, audio) | File type sniffing | Convert to pages; audio, video and imaging become exhibits | "This recording is stored but Avodeck can't read it." | A |
| F37 | Foreign-language page | Language detection | Flag; no machine translation | "This page isn't in English. Ask for a certified translation?" (language from the pack) | A |
| F38 | Support needs to see a case | Support request | Metadata only unless the named expert grants access | Grant shown on the case and in its audit trail | O |
| F39 | Someone guesses or replays authenticator codes | `mfa_attempts` | 5 wrong codes in 15 minutes lock MFA (429) on every device; a code already accepted is refused; a recovery code signs out every other device | "Too many tries. Wait 15 minutes and try again." | Foundation |
| F40 | A stored file part is missing, moved or changed | Chunk sealed to its own path; whole-file SHA-256 re-checked while downloading | Download stops with an error; never sends a different file | "We couldn't open this file. Please contact support." | Foundation |

Each row becomes at least one test in the owning slice. F5, F6, F10, F11, F24 and F28 are tested in `api/tests/guards/`.
