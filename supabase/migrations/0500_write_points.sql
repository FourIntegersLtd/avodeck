-- 0500 write (slice E): points for and against, and range notes (architecture 4.6, screen 14).
-- * points: one verified fact placed on a side of one question. Suggested by the worker (T-14a, origin
--   `ai`, a short `reason` label only) or added by the expert (origin `expert`, starts accepted). Status
--   (state-machines.md, Point): suggested -> accepted / dismissed (reason); either can change back;
--   `stale` is set only by the fact-change trigger (0504) and cleared when the expert decides again,
--   which records the fact's current version. Side codes are core; their labels come from the pack.
-- * range_notes: what other reasonable clinicians might have done, one per question, the expert's words
--   (encrypted under the case key). Only the named expert writes (`opinion.write`); nothing is deleted.

set role app_migrator;

create table app.points (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_id uuid not null references app.questions (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  side text not null check (side in ('supports_claim', 'supports_clinician', 'neutral')),
  status text not null default 'suggested' check (status in ('suggested', 'accepted', 'dismissed', 'stale')),
  origin text not null check (origin in ('ai', 'expert')),
  reason text check (length(reason) <= 80),
  dismissed_reason text check (length(dismissed_reason) between 1 and 200),
  fact_version integer not null,
  job_id uuid references app.jobs (id) on delete set null,
  model_task text check (model_task ~ '^T-[0-9A-Za-z]+$'),
  prompt_version text check (length(prompt_version) <= 40),
  decided_by uuid,
  decided_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (question_id, fact_id),
  check (status <> 'dismissed' or dismissed_reason is not null)
);
create index points_case_question on app.points (case_id, question_id);
create index points_fact on app.points (fact_id);

create function app.points_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  current_version integer;
  marking_stale boolean := pg_trigger_depth() > 1;  -- only the fact-change trigger (0504) updates from inside a trigger
begin
  select f.version into current_version from app.facts f where f.id = new.fact_id and f.case_id = new.case_id;
  if tg_op = 'INSERT' then
    if current_version is null then
      perform app.refuse('a point cites a fact of its own case');
    end if;
    if not exists (select 1 from app.questions q where q.id = new.question_id and q.case_id = new.case_id) then
      perform app.refuse('a point answers a question of its own case');
    end if;
    if (new.origin, new.status) not in (('ai', 'suggested'), ('expert', 'accepted')) then
      perform app.refuse('a suggested point starts suggested and an added point starts accepted');
    end if;
    new.fact_version := current_version;
    new.version := 1;
    if new.origin = 'expert' then
      new.decided_by := app.current_person_id();
      new.decided_at := now();
    end if;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.question_id is distinct from old.question_id
     or new.fact_id is distinct from old.fact_id or new.origin is distinct from old.origin
     or new.reason is distinct from old.reason then
    perform app.refuse('a point keeps its fact, question and suggestion');
  end if;
  if new.status = 'stale' and old.status <> 'stale' and not marking_stale then
    perform app.refuse('only a change to the fact makes a point stale');
  end if;
  if new.status is distinct from old.status and new.status <> 'stale' and (old.status, new.status) not in (
    ('suggested', 'accepted'), ('suggested', 'dismissed'), ('accepted', 'dismissed'), ('dismissed', 'accepted'),
    ('stale', 'accepted'), ('stale', 'dismissed')
  ) then
    perform app.refuse(format('point status cannot change from %s to %s', old.status, new.status));
  end if;
  if not marking_stale and (new.status is distinct from old.status or new.side is distinct from old.side) then
    new.fact_version := current_version;
    new.decided_by := app.current_person_id();
    new.decided_at := now();
  else
    new.fact_version := old.fact_version;
  end if;
  if new.status = 'accepted' then
    new.dismissed_reason := null;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.points_guard() from public;
create trigger points_guard before insert or update on app.points
  for each row execute function app.points_guard();

create table app.range_notes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_id uuid not null unique references app.questions (id) on delete cascade,
  text bytea not null,
  transcript_id uuid,
  updated_by uuid,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index range_notes_case on app.range_notes (case_id);

create function app.range_notes_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.questions q where q.id = new.question_id and q.case_id = new.case_id) then
    perform app.refuse('a range note belongs to a question of its own case');
  end if;
  new.updated_by := app.current_person_id();
  if tg_op = 'INSERT' then
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.question_id is distinct from old.question_id then
    perform app.refuse('a range note stays with its question');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.range_notes_guard() from public;
create trigger range_notes_guard before insert or update on app.range_notes
  for each row execute function app.range_notes_guard();

revoke delete on app.points, app.range_notes from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.points enable row level security;
alter table app.range_notes enable row level security;

create policy points_view on app.points for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy points_add on app.points for insert to app_api
  with check (origin = 'expert' and app.can_access_case(case_id, 'opinion.write'));
create policy points_decide on app.points for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy points_job on app.points to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy range_notes_view on app.range_notes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy range_notes_add on app.range_notes for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy range_notes_change on app.range_notes for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));

reset role;
