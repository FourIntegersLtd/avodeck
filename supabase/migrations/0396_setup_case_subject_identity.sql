-- 0396 setup (slice C): the subject's own name, other names and date of birth, sealed (rule 7: the
-- identifier recognisers can only hide names they are given; see the ADR "Store the subject's name and date
-- of birth, sealed").
-- * Every value is bytes sealed with the case key (purposes subject_name, subject_other_names, subject_dob);
--   other_names seals a JSON list of strings and date_of_birth an ISO date.
-- * Only the named expert writes and reads through the API (row-level security repeats the check, rule 9).
--   Other case members learn only whether an identity is recorded, through app.case_identity_recorded().
-- * The worker reads inside a job for the case (to strip the values before a model call) and never writes.
-- * Deleted with the case: purged by 0397, and the case key's destruction makes the bytes unreadable.

set role app_migrator;

create table app.case_subject_identity (
  case_id uuid primary key references app.cases (id) on delete cascade,
  name bytea not null,
  other_names bytea,
  date_of_birth bytea,
  updated_at timestamptz not null default now()
);

create function app.case_subject_identity_touch() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger case_subject_identity_touch before update on app.case_subject_identity
  for each row execute function app.case_subject_identity_touch();

create function app.case_identity_recorded(p_case_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select app.can_access_case(p_case_id, 'case.view_metadata')
    and exists (select 1 from app.case_subject_identity where case_id = p_case_id)
$$;
grant execute on function app.case_identity_recorded(uuid) to app_api;

revoke delete on app.case_subject_identity from app_api, app_worker;
revoke insert, update on app.case_subject_identity from app_worker;

alter table app.case_subject_identity enable row level security;

-- `conflict.declare` is the named-expert-only action (docs/spec/permissions.md).
create policy case_subject_identity_view on app.case_subject_identity for select to app_api
  using (app.can_access_case(case_id, 'conflict.declare'));
create policy case_subject_identity_add on app.case_subject_identity for insert to app_api
  with check (app.can_access_case(case_id, 'conflict.declare'));
create policy case_subject_identity_change on app.case_subject_identity for update to app_api
  using (app.can_access_case(case_id, 'conflict.declare')) with check (app.can_access_case(case_id, 'conflict.declare'));
create policy case_subject_identity_job on app.case_subject_identity for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
