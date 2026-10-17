-- 0330 setup (slice C): assumptions and the record of each letter's reading (architecture 4.3, screen 06).
-- * assumptions: what the letter tells the expert to assume, its exact words kept (`text_verbatim` never
--   changes), like `questions`: suggested -> confirmed or edited; a newer instruction version on a case
--   never served returns a confirmed or edited assumption to suggested. Changes need `question.confirm`.
-- * instruction_extractions: one row per instruction version read by the `extract_questions` job: how
--   many questions and assumptions were kept, how many suggestions were refused by the word check, and
--   whether the new letter reopened a confirmed set. Counts only, never text (rule 6).

set role app_migrator;

create table app.assumptions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  instruction_version_id uuid,
  number integer not null check (number > 0),
  text_verbatim text not null,
  source_word_ids text[] not null default '{}',
  para_ref text check (length(para_ref) <= 20),
  status text not null default 'suggested' check (status in ('suggested', 'confirmed', 'edited')),
  confirmed_text text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (case_id, instruction_version_id) references app.instruction_versions (case_id, id)
);
create index assumptions_case on app.assumptions (case_id, number);
create index assumptions_instruction_version on app.assumptions (instruction_version_id);

create function app.assumptions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  reopened boolean := false;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new assumption starts suggested');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.text_verbatim is distinct from old.text_verbatim then
    perform app.refuse('an assumption keeps its exact words');
  end if;
  if old.status in ('confirmed', 'edited') and new.status = 'suggested'
     and new.instruction_version_id is distinct from old.instruction_version_id
     and not app.case_ever_served(old.case_id) then
    reopened := coalesce((select v.version_no from app.instruction_versions v where v.id = new.instruction_version_id)
                         > coalesce((select v.version_no from app.instruction_versions v
                                     where v.id = old.instruction_version_id), 0), false);
  end if;
  if new.status is distinct from old.status and not reopened
     and (old.status, new.status) not in (('suggested', 'confirmed'), ('suggested', 'edited')) then
    perform app.refuse(format('assumption status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger assumptions_guard before insert or update on app.assumptions
  for each row execute function app.assumptions_guard();

create table app.instruction_extractions (
  instruction_version_id uuid primary key,
  case_id uuid not null references app.cases (id) on delete cascade,
  job_id uuid references app.jobs (id) on delete set null,
  questions integer not null check (questions >= 0),
  assumptions integer not null check (assumptions >= 0),
  rejected integer not null check (rejected >= 0),
  reopened boolean not null default false,
  model_task text not null check (model_task ~ '^T-[0-9A-Z]+$'),
  prompt_version text not null check (length(prompt_version) <= 40),
  created_at timestamptz not null default now(),
  foreign key (case_id, instruction_version_id) references app.instruction_versions (case_id, id) on delete cascade
);

revoke delete on app.assumptions from app_api, app_worker;
revoke update, delete on app.instruction_extractions from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.assumptions enable row level security;
alter table app.instruction_extractions enable row level security;

create policy assumptions_view on app.assumptions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy assumptions_add on app.assumptions for insert to app_api
  with check (app.can_access_case(case_id, 'question.confirm'));
create policy assumptions_change on app.assumptions for update to app_api
  using (app.can_access_case(case_id, 'question.confirm'))
  with check (app.can_access_case(case_id, 'question.confirm'));
create policy assumptions_job on app.assumptions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy instruction_extractions_view on app.instruction_extractions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy instruction_extractions_job on app.instruction_extractions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
