-- 0340 setup (slice C): details suggested from the letter of instruction (architecture 4.3 and 6.3 T-04, screen 04).
-- * prefill_suggestions: one suggestion per field the `prefill_case` job found in the letter, after the word
--   check (rule 2). The value is encrypted under the case key; `word_ids` cite the letter's words. Nothing is
--   applied to the case: the expert accepts or dismisses each one (suggested -> accepted | dismissed, once),
--   and screen 04 fills its form from what was accepted. A repeat run adds only suggestions not seen before.
-- * prefill_runs: one row per run: how many suggestions were kept and refused. Counts only (rule 6).

set role app_migrator;

create table app.prefill_suggestions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  instruction_version_id uuid not null,
  field text not null check (field in ('instructing_party_name', 'instructing_party_type', 'party_role',
                                       'due_date', 'court_track')),
  value bytea not null,
  word_ids text[] not null check (cardinality(word_ids) > 0),
  status text not null default 'suggested' check (status in ('suggested', 'accepted', 'dismissed')),
  decided_by uuid references app.people (id),
  decided_at timestamptz,
  model_task text not null check (model_task ~ '^T-[0-9A-Z]+$'),
  prompt_version text not null check (length(prompt_version) <= 40),
  created_at timestamptz not null default now(),
  unique (case_id, instruction_version_id, field, word_ids),
  foreign key (case_id, instruction_version_id) references app.instruction_versions (case_id, id) on delete cascade
);

create function app.prefill_suggestions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new suggestion starts suggested');
    end if;
    return new;
  end if;
  if (new.case_id, new.instruction_version_id, new.field, new.value, new.word_ids)
     is distinct from (old.case_id, old.instruction_version_id, old.field, old.value, old.word_ids) then
    perform app.refuse('a suggestion keeps what it suggested');
  end if;
  if old.status <> 'suggested' or new.status not in ('accepted', 'dismissed') then
    perform app.refuse(format('suggestion status cannot change from %s to %s', old.status, new.status));
  end if;
  new.decided_by := app.current_person_id();
  new.decided_at := now();
  return new;
end
$$;
create trigger prefill_suggestions_guard before insert or update on app.prefill_suggestions
  for each row execute function app.prefill_suggestions_guard();

create table app.prefill_runs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  job_id uuid references app.jobs (id) on delete set null,
  kept integer not null check (kept >= 0),
  rejected integer not null check (rejected >= 0),
  created_at timestamptz not null default now()
);
create index prefill_runs_case on app.prefill_runs (case_id, created_at desc);

revoke delete on app.prefill_suggestions from app_api, app_worker;
revoke update, delete on app.prefill_runs from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.prefill_suggestions enable row level security;
alter table app.prefill_runs enable row level security;

create policy prefill_suggestions_view on app.prefill_suggestions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy prefill_suggestions_decide on app.prefill_suggestions for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup'))
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy prefill_suggestions_job on app.prefill_suggestions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy prefill_runs_view on app.prefill_runs for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy prefill_runs_job on app.prefill_runs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
