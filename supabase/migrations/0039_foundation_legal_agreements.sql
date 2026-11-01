-- 0039 foundation: legal agreement acceptance records.
--
-- Tracks which version of terms, privacy policy and DPA each person (and each
-- organisation) accepted, and when.  A row is written at sign-up and at
-- organisation creation, and again whenever the person accepts a newer version.
-- The table is append-only: old rows are never updated or deleted.

set role app_migrator;

create table app.legal_agreements (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references app.people (id),
  organisation_id uuid references app.organisations (id),
  document text not null check (document in ('terms', 'privacy', 'dpa')),
  version text not null,
  accepted_at timestamptz not null default now(),
  ip_address text,
  user_agent text
);

create index legal_agreements_person on app.legal_agreements (person_id, document);
create index legal_agreements_org on app.legal_agreements (organisation_id, document)
  where organisation_id is not null;

alter table app.legal_agreements enable row level security;

-- The person can read their own acceptance records; organisation owners/admins
-- can read their organisation's records.
create policy legal_agreements_read on app.legal_agreements
  for select using (
    person_id = current_setting('app.person_id', true)::uuid
    or (organisation_id is not null and exists (
      select 1 from app.memberships m
      where m.organisation_id = legal_agreements.organisation_id
        and m.person_id = current_setting('app.person_id', true)::uuid
        and m.role in ('owner', 'admin')
        and m.ended_at is null
    ))
  );

-- Only the API role inserts (during sign-up and org creation).
grant select on app.legal_agreements to app_api;
grant insert on app.legal_agreements to app_api;

revoke update, delete on app.legal_agreements from app_api;
revoke all on app.legal_agreements from app_worker;
grant select on app.legal_agreements to app_worker;

reset role;
