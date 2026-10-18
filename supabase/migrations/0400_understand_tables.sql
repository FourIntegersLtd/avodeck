-- 0400 understand (slice D): links, availability overrides, findings, notes, comments, check settings
-- (architecture 4.5, screens 11-13).
-- * fact_question_links: a fact tied to a question. Readers and the `suggest_links` job may only
--   suggest (`link.suggest`); the named expert confirms, rejects or adds one by hand (`link.decide`).
--   A link never goes back to suggested; `stale` when its fact or question has gone.
-- * availability_overrides: append-only; the named expert says when a fact really arrived, or that
--   nobody can tell, always with a reason (`availability.override`). The latest one wins.
-- * findings: "Things to look at", written by the `rerun_checks` job. A pointer to facts, never a
--   conclusion (rule 1). Only the named expert decides (`finding.decide`); "not relevant" and
--   "explained elsewhere" need a reason; the job never sets or clears a decision, it only marks a
--   finding it no longer finds as `gone`.
-- * check_runs: which pack version and checks each run used.
-- * case_guideline_versions: the guideline table entry a case was last shown, to notice changes.
-- * consultation_notes: the named expert's own note on one consultation (`consultation.note`).
-- * comments: any reader may comment on a page, fact or paragraph (`comment.write`); only the author
--   changes the words; anyone who may comment may resolve. Never part of a report.
-- * check_config: each person's own check settings (on/off, window), not a case table.

set role app_migrator;

create table app.fact_question_links (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  question_id uuid not null references app.questions (id) on delete cascade,
  source text not null check (source in ('ai', 'expert')),
  status text not null default 'suggested' check (status in ('suggested', 'confirmed', 'rejected', 'stale')),
  reason text check (length(reason) <= 80),
  decided_by uuid references app.people (id),
  decided_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, fact_id, question_id)
);
create index fact_question_links_question on app.fact_question_links (case_id, question_id);

create function app.fact_question_links_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if (new.source, new.status) not in (('ai', 'suggested'), ('expert', 'confirmed')) then
      perform app.refuse('a link starts suggested, or confirmed when the expert adds it');
    end if;
    new.version := 1;
    return new;
  end if;
  if (new.case_id, new.fact_id, new.question_id, new.source)
     is distinct from (old.case_id, old.fact_id, old.question_id, old.source) then
    perform app.refuse('a link keeps its fact, question and source');
  end if;
  if new.status = 'suggested' and old.status <> 'suggested' then
    perform app.refuse('a decided link never goes back to suggested');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger fact_question_links_guard before insert or update on app.fact_question_links
  for each row execute function app.fact_question_links_guard();

create table app.availability_overrides (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  available_at timestamptz,
  unknown boolean not null default false,
  reason text not null check (length(btrim(reason)) between 1 and 200),
  person_id uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  check (unknown = (available_at is null))
);
create index availability_overrides_fact on app.availability_overrides (case_id, fact_id, created_at);

create table app.check_runs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  job_id uuid references app.jobs (id) on delete set null,
  pack_version text not null check (length(pack_version) <= 80),
  checks jsonb not null default '[]',
  created_at timestamptz not null default now()
);
create index check_runs_case on app.check_runs (case_id, created_at);

create table app.findings (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  check_key text not null check (check_key ~ '^[a-z0-9_]{1,60}$'),
  rule text not null check (rule ~ '^[a-z0-9_]{1,60}$'),
  gentle boolean not null default false,
  evidence_fact_ids uuid[] not null check (cardinality(evidence_fact_ids) > 0),
  evidence_page_ids uuid[] not null default '{}',
  status text not null default 'open' check (status in ('open', 'gone')),
  decision text check (decision in ('relevant', 'not_relevant', 'explained_elsewhere')),
  reason text check (length(reason) <= 200),
  decided_by uuid references app.people (id),
  decided_at timestamptz,
  pack_version text not null check (length(pack_version) <= 80),
  run_id uuid references app.check_runs (id) on delete set null,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index findings_case on app.findings (case_id, status);

create function app.findings_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.decision is not null or new.reason is not null then
      perform app.refuse('a new finding has no decision');
    end if;
    new.version := 1;
    return new;
  end if;
  if (new.case_id, new.check_key, new.rule, new.evidence_fact_ids)
     is distinct from (old.case_id, old.check_key, old.rule, old.evidence_fact_ids) then
    perform app.refuse('a finding keeps its check and the facts it rests on');
  end if;
  if (new.decision, new.reason) is distinct from (old.decision, old.reason) then
    if app.current_person_id() is null then
      perform app.refuse('only the expert decides a finding');
    end if;
    if new.decision is null then
      perform app.refuse('a decision is changed, never cleared');
    end if;
    if new.decision in ('not_relevant', 'explained_elsewhere') and length(btrim(coalesce(new.reason, ''))) = 0 then
      perform app.refuse('this decision needs a reason');
    end if;
    new.decided_by := app.current_person_id();
    new.decided_at := now();
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger findings_guard before insert or update on app.findings
  for each row execute function app.findings_guard();

create table app.case_guideline_versions (
  case_id uuid not null references app.cases (id) on delete cascade,
  code text not null check (length(code) <= 40),
  version_label text not null check (length(version_label) <= 40),
  table_reviewed_at date,
  created_at timestamptz not null default now(),
  primary key (case_id, code)
);

create table app.consultation_notes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  body text not null check (length(body) <= 20000),
  person_id uuid not null references app.people (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, fact_id)
);

create function app.consultation_notes_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if (new.case_id, new.fact_id, new.person_id) is distinct from (old.case_id, old.fact_id, old.person_id) then
    perform app.refuse('a note stays on its consultation');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger consultation_notes_guard before update on app.consultation_notes
  for each row execute function app.consultation_notes_guard();

create table app.comments (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  target_kind text not null check (target_kind in ('page', 'fact', 'paragraph')),
  target_id text not null check (length(target_id) <= 80),
  body text not null check (length(btrim(body)) between 1 and 4000),
  author_id uuid not null references app.people (id),
  resolved_at timestamptz,
  resolved_by uuid references app.people (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index comments_target on app.comments (case_id, target_kind, target_id);

create function app.comments_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if (new.case_id, new.target_kind, new.target_id, new.author_id)
     is distinct from (old.case_id, old.target_kind, old.target_id, old.author_id) then
    perform app.refuse('a comment stays where it was written');
  end if;
  if new.body is distinct from old.body and old.author_id is distinct from app.current_person_id() then
    perform app.refuse('only the author changes a comment');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger comments_guard before update on app.comments
  for each row execute function app.comments_guard();

create table app.check_config (
  person_id uuid not null references app.people (id) on delete cascade,
  check_key text not null check (check_key ~ '^[a-z0-9_]{1,60}$'),
  enabled boolean not null default true,
  window_days integer check (window_days between 1 and 3650),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (person_id, check_key)
);

revoke update, delete on app.availability_overrides from app_api, app_worker;
revoke update, delete on app.check_runs from app_api, app_worker;
revoke delete on app.fact_question_links, app.findings, app.comments from app_api, app_worker;
revoke insert, update, delete on app.availability_overrides, app.consultation_notes, app.comments from app_worker;
revoke all on app.check_config from app_worker;
grant select on app.check_config to app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.fact_question_links enable row level security;
alter table app.availability_overrides enable row level security;
alter table app.check_runs enable row level security;
alter table app.findings enable row level security;
alter table app.case_guideline_versions enable row level security;
alter table app.consultation_notes enable row level security;
alter table app.comments enable row level security;
alter table app.check_config enable row level security;

create policy fact_question_links_view on app.fact_question_links for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy fact_question_links_add on app.fact_question_links for insert to app_api
  with check (app.can_access_case(case_id, 'link.decide')
              or (app.can_access_case(case_id, 'link.suggest') and source = 'ai' and status = 'suggested'));
create policy fact_question_links_change on app.fact_question_links for update to app_api
  using (app.can_access_case(case_id, 'link.decide')) with check (app.can_access_case(case_id, 'link.decide'));
create policy fact_question_links_job on app.fact_question_links to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy availability_overrides_view on app.availability_overrides for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy availability_overrides_add on app.availability_overrides for insert to app_api
  with check (app.can_access_case(case_id, 'availability.override') and person_id = app.current_person_id());
create policy availability_overrides_job on app.availability_overrides for select to app_worker
  using (case_id = app.current_job_case_id());

create policy check_runs_view on app.check_runs for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy check_runs_job on app.check_runs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy findings_view on app.findings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy findings_change on app.findings for update to app_api
  using (app.can_access_case(case_id, 'finding.decide')) with check (app.can_access_case(case_id, 'finding.decide'));
create policy findings_job on app.findings to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy case_guideline_versions_view on app.case_guideline_versions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_guideline_versions_job on app.case_guideline_versions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy consultation_notes_view on app.consultation_notes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy consultation_notes_add on app.consultation_notes for insert to app_api
  with check (app.can_access_case(case_id, 'consultation.note') and person_id = app.current_person_id());
create policy consultation_notes_change on app.consultation_notes for update to app_api
  using (app.can_access_case(case_id, 'consultation.note') and person_id = app.current_person_id())
  with check (app.can_access_case(case_id, 'consultation.note') and person_id = app.current_person_id());
create policy consultation_notes_job on app.consultation_notes for select to app_worker
  using (case_id = app.current_job_case_id());

create policy comments_view on app.comments for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy comments_add on app.comments for insert to app_api
  with check (app.can_access_case(case_id, 'comment.write') and author_id = app.current_person_id());
create policy comments_change on app.comments for update to app_api
  using (app.can_access_case(case_id, 'comment.write')) with check (app.can_access_case(case_id, 'comment.write'));

create policy check_config_own on app.check_config to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());
create policy check_config_job on app.check_config for select to app_worker
  using (person_id = (select c.named_expert_id from app.cases c where c.id = app.current_job_case_id()));

reset role;
