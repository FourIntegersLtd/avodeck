-- 0320 setup (slice C): conflicts of interest and withdrawal (architecture 4.3, screen 05, scenario S16).
-- * conflict_answers: the expert's answer to each of the jurisdiction pack's conflict questions (keys from
--   `conflicts.yaml`); a "yes" carries the expert's detail, encrypted under the case key. Only the named
--   expert answers (`conflict.declare`).
-- * conflict_declarations: each time the expert declares (at the start, or mid-case when something
--   changes). Append-only; the latest row is the current declaration.
-- * case_withdrawals: why the expert withdrew (encrypted). One per case; the case's status moves to
--   `withdrawn` in the same transaction. Served reports stay locked (their own guard).
-- * app.expert_covers_period(): does the named expert's practice (roles whose setting is one the specialty
--   counts) cover the case period? Returns 'covered', 'partial', 'none' or 'unknown' (no period yet) to
--   anyone who may view the case's content; the roles themselves stay private to the expert.

set role app_migrator;

create table app.conflict_answers (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_key text not null check (question_key ~ '^[a-z][a-z0-9_]{0,39}$'),
  answer text not null check (answer in ('yes', 'no')),
  detail bytea,
  answered_by uuid references app.people (id) default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, question_key),
  check (answer = 'yes' or detail is null)
);
create trigger conflict_answers_version before insert or update on app.conflict_answers
  for each row execute function app.setup_bump_version();

create table app.conflict_declarations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  outcome text not null check (outcome in ('none', 'disclosed')),
  declared_by uuid references app.people (id) default app.current_person_id(),
  created_at timestamptz not null default now()
);
create index conflict_declarations_case on app.conflict_declarations (case_id, created_at desc);

create table app.case_withdrawals (
  case_id uuid primary key references app.cases (id) on delete cascade,
  reason bytea not null,
  withdrawn_by uuid references app.people (id) default app.current_person_id(),
  created_at timestamptz not null default now()
);

revoke delete on app.conflict_answers from app_api, app_worker;
revoke update, delete on app.conflict_declarations from app_api, app_worker;
revoke update, delete on app.case_withdrawals from app_api, app_worker;

create function app.expert_covers_period(p_case_id uuid, p_settings text[]) returns text
  language sql stable
  security definer
  set search_path = ''
as $$
  select case
    when not app.can_access_case(c.id, 'case.view_content') then null
    when c.period_start is null or c.period_end is null then 'unknown'
    when daterange(c.period_start, c.period_end, '[]') <@ coalesce(
           (select range_agg(r.period) from app.expert_roles r
             where r.expert_id = c.named_expert_id and r.specialty = c.specialty and r.setting = any (p_settings)),
           '{}'::datemultirange) then 'covered'
    when exists (select 1 from app.expert_roles r
                  where r.expert_id = c.named_expert_id and r.specialty = c.specialty and r.setting = any (p_settings)
                    and r.period && daterange(c.period_start, c.period_end, '[]')) then 'partial'
    else 'none'
  end
  from app.cases c where c.id = p_case_id
$$;
revoke all on function app.expert_covers_period(uuid, text[]) from public;
grant execute on function app.expert_covers_period(uuid, text[]) to app_api;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.conflict_answers enable row level security;
alter table app.conflict_declarations enable row level security;
alter table app.case_withdrawals enable row level security;

create policy conflict_answers_view on app.conflict_answers for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy conflict_answers_add on app.conflict_answers for insert to app_api
  with check (app.can_access_case(case_id, 'conflict.declare'));
create policy conflict_answers_change on app.conflict_answers for update to app_api
  using (app.can_access_case(case_id, 'conflict.declare'))
  with check (app.can_access_case(case_id, 'conflict.declare'));

create policy conflict_declarations_view on app.conflict_declarations for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy conflict_declarations_add on app.conflict_declarations for insert to app_api
  with check (app.can_access_case(case_id, 'conflict.declare'));

create policy case_withdrawals_view on app.case_withdrawals for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_withdrawals_add on app.case_withdrawals for insert to app_api
  with check (app.can_access_case(case_id, 'case.withdraw'));

reset role;
