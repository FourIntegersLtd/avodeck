-- 0014 foundation: MFA code entry limits (architecture 9; RFC 6238 section 5.2).
-- One row per person: wrong codes in the current 15-minute window (five lock code entry until the
-- window ends, on every device), and the hash of the last code that worked (it cannot be used
-- again while it could still be valid). Only hashes are kept; the row is the person's own.

set role app_migrator;

create table app.mfa_attempts (
  person_id uuid primary key references app.people (id) on delete cascade,
  failures integer not null default 0 check (failures >= 0),
  window_started_at timestamptz not null default now(),
  last_code_hash text,
  last_code_at timestamptz
);

alter table app.mfa_attempts enable row level security;
revoke all on app.mfa_attempts from app_worker;
revoke delete on app.mfa_attempts from app_api;
create policy mfa_attempts_self on app.mfa_attempts to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

reset role;
