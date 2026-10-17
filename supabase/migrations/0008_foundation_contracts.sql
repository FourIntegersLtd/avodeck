-- 0008 foundation: the contract tables slices fill (architecture 4, 8.3) and the upload sweep.
--
-- * `facts`: a value read from a numbered page, citing OCR word ids (rule 2). Starts `suggested`;
--   follows the fact state machine; every change raises `version`; never deleted by the running app.
-- * `questions`: the questions asked, with their exact words kept (`text_verbatim` never changes).
-- * `report_versions`: once `locked_at` is set the row is immutable (rule 8), except locked -> served
--   and served -> withdrawn. Refusals raise SQLSTATE `AVLCK` (409 `locked` through the API).
-- * `app.expire_uploads(run_date)`: the daily sweep marks unfinished uploads past expiry `expired`
--   and audits each one. Worker only.
-- Kinds, types and reasons are short codes from the case's packs; nothing here names a country.

set role app_migrator;

-- Facts --------------------------------------------------------------------------------------
create table app.facts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  fields jsonb not null default '{}',
  value text,
  word_ids text[] not null check (cardinality(word_ids) > 0),
  bbox jsonb,
  event_date date,
  date_basis text check (date_basis ~ '^[a-z][a-z0-9_]{0,39}$'),
  available_at timestamptz,
  availability_basis text check (availability_basis ~ '^[a-z][a-z0-9_]{0,39}$'),
  status text not null default 'suggested' check (status in ('suggested', 'verified', 'edited', 'rejected')),
  version integer not null default 1,
  model_task text,
  model_id text,
  prompt_version text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index facts_case_page on app.facts (case_id, page_id);
create index facts_case_status on app.facts (case_id, status);

create function app.facts_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new fact starts suggested');
    end if;
    if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
      perform app.refuse('a fact cites a page of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.page_id is distinct from old.page_id then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified')
  ) then
    perform app.refuse(format('fact status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create trigger facts_guard before insert or update on app.facts
  for each row execute function app.facts_guard();

-- Questions ----------------------------------------------------------------------------------
create table app.questions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  instruction_version_id uuid,
  number integer not null check (number > 0),
  text_verbatim text not null,
  source_word_ids text[] not null default '{}',
  para_ref text,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  outside_expertise boolean not null default false,
  status text not null default 'suggested' check (status in ('suggested', 'confirmed', 'edited')),
  confirmed_text text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index questions_case on app.questions (case_id, number);

create function app.questions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new question starts suggested');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.text_verbatim is distinct from old.text_verbatim then
    perform app.refuse('a question keeps its exact words');
  end if;
  if new.status is distinct from old.status
     and (old.status, new.status) not in (('suggested', 'confirmed'), ('suggested', 'edited')) then
    perform app.refuse(format('question status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create trigger questions_guard before insert or update on app.questions
  for each row execute function app.questions_guard();

-- Report versions ----------------------------------------------------------------------------
create table app.report_versions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  version_no integer not null check (version_no > 0),
  type text not null check (type ~ '^[a-z][a-z0-9_]{0,39}$'),
  display_name text not null check (length(display_name) between 1 and 200),
  docx_path text,
  pdf_path text,
  sha256 text check (sha256 ~ '^[0-9a-f]{64}$'),
  status text not null default 'draft' check (status in ('draft', 'locked', 'served', 'withdrawn')),
  locked_at timestamptz,
  withdrawn_at timestamptz,
  withdrawn_reason text check (withdrawn_reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  prev_version_id uuid references app.report_versions (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, version_no)
);

create function app.report_locked(p_message text) returns void
  language plpgsql
  set search_path = ''
as $$
begin
  raise exception using errcode = 'AVLCK', message = p_message;
end
$$;

create function app.report_versions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  moving constant text[] := array['status', 'updated_at', 'withdrawn_reason', 'withdrawn_at'];
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' or new.locked_at is not null or new.withdrawn_at is not null then
      perform app.refuse('a new report version starts as a draft');
    end if;
    return new;
  end if;
  if old.locked_at is not null then
    if (old.status, new.status) not in (('locked', 'served'), ('served', 'withdrawn')) then
      perform app.report_locked('this report version is locked');
    end if;
    if new.status = 'served' and (new.withdrawn_reason is not null or new.withdrawn_at is not null) then
      perform app.report_locked('this report version is locked');
    end if;
    if new.status = 'withdrawn' and new.withdrawn_reason is null then
      perform app.refuse('withdrawing a served report needs a reason');
    end if;
    if (to_jsonb(new) - moving) is distinct from (to_jsonb(old) - moving) then
      perform app.report_locked('this report version is locked');
    end if;
    if new.status = 'withdrawn' then
      new.withdrawn_at := now();
    end if;
    new.updated_at := now();
    return new;
  end if;
  if new.locked_at is not null and new.status = 'draft' then
    perform app.refuse('only locking sets locked_at');
  end if;
  if new.status = 'locked' then
    if new.sha256 is null then
      perform app.refuse('locking a report version needs its sha256');
    end if;
    new.locked_at := now();
  elsif new.status <> 'draft' then
    perform app.refuse(format('report version status cannot change from %s to %s', old.status, new.status));
  end if;
  new.updated_at := now();
  return new;
end
$$;

create trigger report_versions_guard before insert or update on app.report_versions
  for each row execute function app.report_versions_guard();

-- Access -------------------------------------------------------------------------------------
alter table app.facts enable row level security;
alter table app.questions enable row level security;
alter table app.report_versions enable row level security;
revoke delete on app.facts, app.questions, app.report_versions from app_api, app_worker;

create policy facts_view on app.facts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy facts_add on app.facts for insert to app_api
  with check (app.can_access_case(case_id, 'fact.verify'));
create policy facts_change on app.facts for update to app_api
  using (app.can_access_case(case_id, 'fact.verify')) with check (app.can_access_case(case_id, 'fact.verify'));
create policy facts_job on app.facts to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy questions_view on app.questions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy questions_add on app.questions for insert to app_api
  with check (app.can_access_case(case_id, 'question.confirm'));
create policy questions_change on app.questions for update to app_api
  using (app.can_access_case(case_id, 'question.confirm'))
  with check (app.can_access_case(case_id, 'question.confirm'));
create policy questions_job on app.questions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy report_versions_view on app.report_versions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_versions_add on app.report_versions for insert to app_api
  with check (app.can_access_case(case_id, 'report.build'));
create policy report_versions_change on app.report_versions for update to app_api
  using (app.can_access_case(case_id, 'report.build')) with check (app.can_access_case(case_id, 'report.build'));
create policy report_versions_job on app.report_versions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- The daily upload sweep ---------------------------------------------------------------------
create function app.expire_uploads(p_run_date date)
  returns table (expired_case_id uuid, expired_upload_id uuid)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  u record;
begin
  for u in
    update app.uploads up set status = 'expired'
    where up.status = 'open' and up.expires_at < least(now(), (p_run_date + 1)::timestamptz)
    returning up.id, up.case_id
  loop
    insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, u.case_id, 'upload.expired', 'upload:' || u.id
    from app.cases c where c.id = u.case_id;
    expired_case_id := u.case_id;
    expired_upload_id := u.id;
    return next;
  end loop;
end
$$;

revoke execute on function app.expire_uploads(date), app.report_locked(text) from public, app_api, app_worker;
grant execute on function app.expire_uploads(date) to app_worker;
grant execute on function app.report_locked(text) to app_api, app_worker;

reset role;
