-- 0505 write (slice E): citable facts the draft does not use yet (T-14b, screen 14).
-- * relevant_facts: one suggestion per question and fact, made by the worker after a draft is saved,
--   with a short `why_relevant` label only (rule 1). Status (state-machines.md, Relevant fact):
--   suggested -> dismissed, by the named expert (`opinion.write`); nothing else changes and nothing is
--   deleted. A suggestion stops showing once the draft cites the fact or the fact is no longer citable.
-- * The worker reads the draft it sends (opinions, select only, its job's case).

set role app_migrator;

create table app.relevant_facts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_id uuid not null references app.questions (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  why_relevant text not null check (length(why_relevant) between 1 and 80),
  status text not null default 'suggested' check (status in ('suggested', 'dismissed')),
  fact_version integer not null,
  job_id uuid references app.jobs (id) on delete set null,
  model_task text check (model_task ~ '^T-[0-9A-Za-z]+$'),
  prompt_version text check (length(prompt_version) <= 40),
  decided_by uuid,
  decided_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (question_id, fact_id)
);
create index relevant_facts_case_question on app.relevant_facts (case_id, question_id);

create function app.relevant_facts_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  current_version integer;
begin
  if tg_op = 'INSERT' then
    select f.version into current_version from app.facts f where f.id = new.fact_id and f.case_id = new.case_id;
    if current_version is null then
      perform app.refuse('a suggestion names a fact of its own case');
    end if;
    if not exists (select 1 from app.questions q where q.id = new.question_id and q.case_id = new.case_id) then
      perform app.refuse('a suggestion belongs to a question of its own case');
    end if;
    if new.status <> 'suggested' then
      perform app.refuse('a suggestion starts suggested');
    end if;
    new.fact_version := current_version;
    new.version := 1;
    return new;
  end if;
  if (to_jsonb(new) - array['status', 'decided_by', 'decided_at', 'version', 'updated_at'])
     is distinct from (to_jsonb(old) - array['status', 'decided_by', 'decided_at', 'version', 'updated_at']) then
    perform app.refuse('a suggestion keeps its fact, question and label');
  end if;
  if new.status is distinct from old.status and (old.status, new.status) <> ('suggested', 'dismissed') then
    perform app.refuse(format('suggestion status cannot change from %s to %s', old.status, new.status));
  end if;
  if new.status is distinct from old.status then
    new.decided_by := app.current_person_id();
    new.decided_at := now();
  else
    new.decided_by := old.decided_by;
    new.decided_at := old.decided_at;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.relevant_facts_guard() from public;
create trigger relevant_facts_guard before insert or update on app.relevant_facts
  for each row execute function app.relevant_facts_guard();

revoke delete on app.relevant_facts from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.relevant_facts enable row level security;

create policy relevant_facts_view on app.relevant_facts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy relevant_facts_decide on app.relevant_facts for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy relevant_facts_job on app.relevant_facts to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy opinions_job_read on app.opinions for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
