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
