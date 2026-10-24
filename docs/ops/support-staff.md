# Support staff: adding and removing them

Avodeck support staff are listed in `app.support_staff` (migration 0030). No API route writes the list: an operator changes it by hand, in SQL, in the cell's own database. Being on the list gives no case content by itself. Support sees a case's metadata through the support console, and a case's content only while the case's named expert has given a grant (`support_access_grants`, at most 24 hours, one case). Every view under a grant is written to that case's audit trail as `support_access.viewed` (`docs/spec/permissions.md`, "Avodeck staff").

## Before adding someone

1. They have an Avodeck account in this cell (they have signed in once, so they have a `people` row).
2. They have set up MFA with an authenticator app. Every support console call and every case request under a grant needs MFA completed in the last 10 minutes; without MFA they cannot use support at all.
3. A second person (the founder, until there is a team) agrees to the change. Record who asked and who agreed in the operations log, outside the database.

## Add a person

Connect to the cell's database as the migration owner (never as `app_api` or `app_worker`; they cannot write this table), then:

```sql
begin;
set local role app_migrator;
-- Look the person up by their sign-in email; check exactly one row comes back.
select id, email, closed_at from app.people where email = lower('person@example.com');
insert into app.support_staff (person_id) values ('<the id from above>');
commit;
```

If they were staff before (a row with `removed_at` set), add them back by clearing it instead:

```sql
update app.support_staff set removed_at = null, added_at = now() where person_id = '<id>';
```

## Remove a person

Never delete the row: set `removed_at`, so the history stays.

```sql
begin;
set local role app_migrator;
update app.support_staff set removed_at = now() where person_id = '<id>' and removed_at is null;
commit;
```

Their access ends at once: any grant they hold stops working on their next request, because the access check reads the list every time. Revoke their sessions too (`DELETE /v1/me/sessions/{session_id}` as them, or set `revoked_at` on their `app.sessions` rows).

## Check who is staff

```sql
select s.person_id, p.email, s.added_at, s.removed_at
from app.support_staff s join app.people p on p.id = s.person_id
order by s.removed_at nulls first, s.added_at;
```

Review the list at every phase gate and whenever someone leaves the company.

# Runbooks

Start every support call in the console (`/support`): look the person up by their sign-in email, or the practice or team by its number. The console shows flags, counts and dates only; never paste a name, an email or case text into a ticket or the operations log (rule 6).

## Reset an expert's MFA factor (lost phone, no backup codes)

Architecture 10.1: without backup codes, identity re-verification, a 24-hour delay and notice to the account email. Organisation owners and admins cannot bypass it, and neither can support. Until there is a console action for this, an operator does it by hand.

1. In the console, look the person up. Note "Backup codes left": if any are left, tell them to sign in with one and set up a new authenticator themselves; stop here.
2. Check who they are outside Avodeck, by a way already on file before the call (for example their professional registration and a call back to a number the practice publishes). Never accept details given only in the call or the email asking. Record the check, the date and who did it in the operations log, outside the database.
3. Email the account's address, from the support mailbox, that a reset was asked for and will happen in 24 hours unless they reply to stop it. Note the time.
4. Wait 24 hours. If anyone replies to stop it, stop and escalate to the founder.
5. Remove the factor in Supabase Auth for that user (dashboard, Authentication, the user, MFA factors), as the project owner.
6. End their sessions and clear what remains of the old factor, as the migration owner:

```sql
begin;
set local role app_migrator;
update app.sessions set revoked_at = now() where person_id = '<id>' and revoked_at is null;
delete from app.mfa_recovery_codes where person_id = '<id>' and used_at is null;
delete from app.mfa_attempts where person_id = '<id>';
commit;
```

7. Tell them to sign in and set up a new authenticator; they get ten new backup codes. Look them up again: "Authenticator set up" shows once they have.

A lock-out after too many wrong codes ("Locked out of the code step until ...") is not a reset: it ends by itself at the time shown. Do not clear `mfa_attempts` for it.

## A file blocked for safety that the expert says is clean (F3)

Every upload is scanned before any page is read (`scan_document` job; the scanner is `VIRUS_SCANNER`, ClamAV in the cloud cell). A flagged file is quarantined and never processed; the expert sees "This file was blocked for safety."

1. Find the case in the console (by its id or the named expert's email) and open it. The case page shows the blocked document's job and its code, never the file.
2. Do not open, download or rescan the file yourself, and never release it from quarantine: there is no route for that, by design.
3. Ask the expert to get a fresh copy from the sender and upload that. If the fresh copy is blocked too, ask the sender to resend it as a plain PDF.
4. If many clean files are blocked across cases, the scanner's signatures may be wrong: escalate to the founder to check the scanner, not the files.

## Paused or stuck work

Run "Check the service" in the console first.

- "The worker has not checked in": nothing runs until the worker is back. Check the worker component in DigitalOcean App Platform and restart it. Work waits safely; nothing is lost.
- Work paused because the reading service is busy or away (`service_busy`, `model_unavailable`, F5): it starts again by itself, waiting longer each time, for up to 2 hours in all; after that the job fails and the expert sees a button to try again. Do nothing unless the outage lasts longer; check the cell's Azure and Bedrock status.
- Work paused for the cost limit (`cost_guard`): it waits for someone to decide. It does not resume by itself. Look at the case page for the job's code; the expert or the founder decides whether to go on.
- Work waiting with `case_on_hold`: the case is on hold; it runs once the case comes off hold. Do not touch the job.
- "Work waiting" that keeps growing while the worker checks in: the queue is behind or a job is stuck running. Open the cases concerned and check their jobs' codes; escalate to the founder with the case ids and codes only.
