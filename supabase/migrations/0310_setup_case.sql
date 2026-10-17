-- 0310 setup (slice C): the case setup tables (architecture 4.3, screen 04).
-- * case_parties: the people and bodies in the case, any number, each with a pack party role key. The
--   name and representative are encrypted under the case key (bytes). No change or removal once the
--   case is served (SQLSTATE AV409, 409 in the API; the API also refuses new parties then).
-- * case_instructing_parties: who instructs the expert, dated. A joint expert has two current rows. A
--   change of firm ends the old row (effective_to) and starts a new one (replaces_id); rows are never
--   deleted, so the history stays.
-- * case_links: links to other cases the person can see; no data is shared through a link.
-- * case_instructions: the text of oral instructions, one per oral instruction version, encrypted, dated,
--   typed or from a dictation transcript.
-- * case_subject_keys: a keyed hash of the subject's identifier for the duplicate-case check. The
--   identifier itself is never stored.

set role app_migrator;

create table app.case_parties (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  role_key text not null check (role_key ~ '^[a-z][a-z0-9_]{0,39}$'),
  name bytea not null,
  representative bytea,
  position integer not null default 0,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index case_parties_case on app.case_parties (case_id, position, created_at);

create table app.case_instructing_parties (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  type_key text not null check (type_key ~ '^[a-z][a-z0-9_]{0,39}$'),
  name text not null check (length(name) between 1 and 200),
  firm text not null check (length(firm) between 1 and 200),
  email text check (length(email) <= 320),
  reference text check (length(reference) <= 100),
  effective_from date not null default current_date,
  effective_to date,
  replaces_id uuid references app.case_instructing_parties (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (effective_to is null or effective_to >= effective_from - 1)
);
create index case_instructing_parties_case on app.case_instructing_parties (case_id, effective_from);

create table app.case_links (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  linked_case_id uuid not null references app.cases (id) on delete cascade,
  kind text not null check (kind in ('related', 'previous_instruction', 'replaces', 'other_proceedings')),
  created_by uuid references app.people (id) default app.current_person_id(),
  created_at timestamptz not null default now(),
  check (case_id <> linked_case_id),
  unique (case_id, linked_case_id, kind)
);

create table app.case_instructions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  instruction_version_id uuid not null unique,
  text bytea not null,
  source text not null default 'typed' check (source in ('typed', 'dictated')),
  transcript_id uuid references app.transcripts (id) on delete set null,
  received_on date not null,
  created_by uuid references app.people (id) default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (case_id, instruction_version_id) references app.instruction_versions (case_id, id)
);
create index case_instructions_case on app.case_instructions (case_id);

create table app.case_subject_keys (
  case_id uuid primary key references app.cases (id) on delete cascade,
  key_hash text not null check (key_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index case_subject_keys_hash on app.case_subject_keys (key_hash);

-- Versions and the served lock ---------------------------------------------------------------------------
create function app.case_parties_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if app.case_ever_served(coalesce(new.case_id, old.case_id)) then
    perform app.refuse('the parties are fixed once the case is served');
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger case_parties_guard before update or delete on app.case_parties
  for each row execute function app.case_parties_guard();

create trigger case_instructing_parties_version before insert or update on app.case_instructing_parties
  for each row execute function app.setup_bump_version();
create trigger case_instructions_version before insert or update on app.case_instructions
  for each row execute function app.setup_bump_version();

create function app.case_subject_keys_touch() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger case_subject_keys_touch before update on app.case_subject_keys
  for each row execute function app.case_subject_keys_touch();

revoke delete on app.case_instructing_parties from app_api, app_worker;
revoke delete on app.case_instructions from app_api, app_worker;
revoke update on app.case_links from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.case_parties enable row level security;
alter table app.case_instructing_parties enable row level security;
alter table app.case_links enable row level security;
alter table app.case_instructions enable row level security;
alter table app.case_subject_keys enable row level security;

create policy case_parties_view on app.case_parties for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_parties_add on app.case_parties for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy case_parties_change on app.case_parties for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup')) with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy case_parties_remove on app.case_parties for delete to app_api
  using (app.can_access_case(case_id, 'case.edit_setup'));

create policy case_instructing_parties_view on app.case_instructing_parties for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_instructing_parties_add on app.case_instructing_parties for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy case_instructing_parties_change on app.case_instructing_parties for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup')) with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy case_links_view on app.case_links for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy case_links_add on app.case_links for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy case_links_remove on app.case_links for delete to app_api
  using (app.can_access_case(case_id, 'case.edit_setup'));

create policy case_instructions_view on app.case_instructions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_instructions_add on app.case_instructions for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy case_instructions_change on app.case_instructions for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));

create policy case_subject_keys_view on app.case_subject_keys for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy case_subject_keys_add on app.case_subject_keys for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy case_subject_keys_change on app.case_subject_keys for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup')) with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy case_parties_job on app.case_parties to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy case_instructing_parties_job on app.case_instructing_parties to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy case_links_job on app.case_links to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy case_instructions_job on app.case_instructions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy case_subject_keys_job on app.case_subject_keys to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
