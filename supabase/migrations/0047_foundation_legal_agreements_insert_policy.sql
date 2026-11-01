-- 0046 foundation: the missing insert policy on app.legal_agreements.
--
-- Migration 0039 enabled row-level security and granted insert to app_api, but never created
-- an insert policy. A grant with no matching policy denies the write: every call to
-- accept_legal() (api/app/core/routes/legal.py) was silently rejected by Postgres, so nobody
-- could ever actually record accepting the terms, privacy policy or DPA.

set role app_migrator;

create policy legal_agreements_insert on app.legal_agreements
  for insert with check (
    person_id = current_setting('app.person_id', true)::uuid
    or (organisation_id is not null and exists (
      select 1 from app.memberships m
      where m.organisation_id = legal_agreements.organisation_id
        and m.person_id = current_setting('app.person_id', true)::uuid
        and m.role in ('owner', 'admin')
        and m.ended_at is null
    ))
  );

reset role;
