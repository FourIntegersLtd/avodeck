-- 0700 after serving (slice G): court directions, deadlines and their history, hearings, rounds of
-- written questions, the other side's expert report and its claims, changes to a served report and new
-- records that may matter (architecture 4.9, 10.7).
--
-- court_directions: an order from the court, kept as entered (never changed). Its words are encrypted.
-- deadlines: every date that matters. `created_from` (pack, direction, manual, round) never changes; `source`
--   says what last moved it. A date can move only with a `deadline_changes` row written in the same
--   transaction (old and new date), so every move is kept with its source. Dates are dates in the
--   court's timezone (`timezone`), so clock changes cannot move them.
-- deadline_changes: append-only history of each deadline (the first row has no old date).
-- hearings: scheduled -> heard / vacated / adjourned (adjourned names the new hearing); finished rows
--   never change. `changed_at` tells slice I its packs for this hearing are stale.
-- written_question_rounds: open -> answered -> served, open -> withdrawn; each with its own deadline
--   and answers document (a report version of type `answers`).
-- written_questions: received -> answering -> answered -> served; nothing changes once answered except
--   the move to served. Question and answer words are encrypted.
-- opposing_reports: one row per document registered as the other side's expert report.
-- opposing_claims: unchecked -> a result (worker); the expert confirms or changes any result (audited).
-- report_changes: a correction or change of opinion to one paragraph of a served version; frozen once the
--   supplementary version it went into is signed. Old and new words and the reason are encrypted.
-- may_matter: a fact on a new page that may bear on a question (T-21a); shown only once the page is read.

set role app_migrator;

-- Court directions -----------------------------------------------------------------------------------------
create table app.court_directions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  made_on date not null,
  reference text check (length(reference) <= 200),
  text bytea,
  document_id uuid references app.documents (id),
  stay boolean not null default false,
  created_deadline_ids uuid[] not null default '{}',
  created_by uuid references app.people (id),
  created_at timestamptz not null default now()
);
create index court_directions_case on app.court_directions (case_id, made_on);

-- Hearings --------------------------------------------------------------------------------------------------
create table app.hearings (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  type text not null check (type ~ '^[a-z][a-z0-9_]{0,39}$'),
  starts_on date not null,
  ends_on date not null,
  venue text check (length(venue) <= 200),
  status text not null default 'scheduled' check (status in ('scheduled', 'heard', 'vacated', 'adjourned')),
  replaced_by_hearing_id uuid references app.hearings (id),
  direction_id uuid references app.court_directions (id),
  version integer not null default 1,
  changed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ends_on >= starts_on)
);
create index hearings_case on app.hearings (case_id, starts_on);

create function app.hearings_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'scheduled' or new.replaced_by_hearing_id is not null then
      perform app.refuse('a new hearing starts as scheduled');
    end if;
    return new;
  end if;
  if old.status <> 'scheduled' then
    perform app.refuse('a finished hearing does not change');
  end if;
  if new.case_id <> old.case_id or new.created_at <> old.created_at then
    perform app.refuse('a hearing stays with its case');
  end if;
  if new.status = 'adjourned' then
    if new.replaced_by_hearing_id is null then
      perform app.refuse('an adjourned hearing names the hearing that replaces it');
    end if;
    if not exists (select 1 from app.hearings h where h.id = new.replaced_by_hearing_id
                   and h.case_id = new.case_id and h.id <> new.id) then
      perform app.refuse('the new hearing belongs to the same case');
    end if;
  elsif new.replaced_by_hearing_id is not null then
    perform app.refuse('only an adjourned hearing is replaced');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  new.changed_at := now();
  return new;
end
$$;
create trigger hearings_guard before insert or update on app.hearings
  for each row execute function app.hearings_guard();

-- Deadlines -------------------------------------------------------------------------------------------------
create table app.deadlines (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  rule_ref text check (length(rule_ref) <= 80),
  due_on date not null,
  calendar text check (calendar ~ '^[a-z][a-z0-9_]{0,39}$'),
  timezone text not null check (length(timezone) between 1 and 64),
  created_from text not null check (created_from in ('pack', 'direction', 'manual', 'round')),
  source text not null check (source in ('pack', 'direction', 'manual', 'round')),
  pack_version text check (length(pack_version) <= 200),
  hearing_id uuid references app.hearings (id),
  done_at timestamptz,
  last_reminded_on date,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index deadlines_case on app.deadlines (case_id, due_on);
create unique index deadlines_one_pack_kind on app.deadlines (case_id, kind) where created_from = 'pack';

create table app.deadline_changes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  deadline_id uuid not null references app.deadlines (id) on delete cascade,
  old_due_on date,
  new_due_on date not null,
  reason text not null check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  source text not null check (source in ('pack', 'direction', 'manual', 'round')),
  source_id uuid,
  note bytea,
  changed_by uuid references app.people (id),
  at timestamptz not null default now()
);
create index deadline_changes_deadline on app.deadline_changes (deadline_id, at);
create index deadline_changes_case on app.deadline_changes (case_id);

create function app.deadline_changes_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.deadlines d where d.id = new.deadline_id and d.case_id = new.case_id) then
    perform app.refuse('a deadline change belongs to a deadline of its own case');
  end if;
  return new;
end
$$;
revoke all on function app.deadline_changes_guard() from public;
create trigger deadline_changes_guard before insert on app.deadline_changes
  for each row execute function app.deadline_changes_guard();

create function app.deadlines_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.kind <> old.kind or new.created_from <> old.created_from
     or new.created_at <> old.created_at or new.timezone <> old.timezone then
    perform app.refuse('a deadline keeps its case, kind, origin and timezone');
  end if;
  if new.due_on <> old.due_on and not exists (
    select 1 from app.deadline_changes c
    where c.deadline_id = new.id and c.old_due_on = old.due_on and c.new_due_on = new.due_on and c.at = now()
  ) then
    perform app.refuse('moving a deadline needs a deadline change with its reason');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.deadlines_guard() from public;
create trigger deadlines_guard before update on app.deadlines
  for each row execute function app.deadlines_guard();

-- Rounds of written questions -------------------------------------------------------------------------------
create table app.written_question_rounds (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  number integer not null check (number > 0),
  from_party_id uuid references app.case_parties (id),
  received_on date not null,
  document_id uuid references app.documents (id),
  deadline_id uuid references app.deadlines (id),
  answers_version_id uuid references app.report_versions (id),
  status text not null default 'open' check (status in ('open', 'answered', 'served', 'withdrawn')),
  extracted_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, number)
);

create function app.written_question_rounds_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'open' or new.answers_version_id is not null then
      perform app.refuse('a new round of questions starts open');
    end if;
    return new;
  end if;
  if new.case_id <> old.case_id or new.number <> old.number then
    perform app.refuse('a round keeps its case and number');
  end if;
  if new.status <> old.status and (old.status, new.status) not in
     (('open', 'answered'), ('answered', 'served'), ('open', 'withdrawn')) then
    perform app.refuse(format('a round cannot move from %s to %s', old.status, new.status));
  end if;
  if old.status in ('served', 'withdrawn') then
    perform app.refuse('a finished round does not change');
  end if;
  if new.status = 'answered' and new.answers_version_id is null then
    perform app.refuse('an answered round names its answers document');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger written_question_rounds_guard before insert or update on app.written_question_rounds
  for each row execute function app.written_question_rounds_guard();

-- Written questions -----------------------------------------------------------------------------------------
create table app.written_questions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  round_id uuid not null references app.written_question_rounds (id) on delete cascade,
  rule_ref text check (length(rule_ref) <= 80),
  number integer not null check (number > 0),
  para_ref text check (length(para_ref) <= 20),
  text_verbatim bytea not null,
  word_ids text[] not null default '{}',
  suggested_classification text check (suggested_classification ~ '^[a-z][a-z0-9_]{0,39}$'),
  classification_reason text check (length(classification_reason) <= 80),
  classification text check (classification ~ '^[a-z][a-z0-9_]{0,39}$'),
  suggested_paragraph_ids uuid[] not null default '{}',
  paragraph_ids uuid[] not null default '{}',
  answer_text bytea,
  citation_refs jsonb not null default '[]' check (jsonb_typeof(citation_refs) = 'array'),
  told_instructing_party_at timestamptz,
  status text not null default 'received' check (status in ('received', 'answering', 'answered', 'served')),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (round_id, number)
);
create index written_questions_case on app.written_questions (case_id);

create function app.written_questions_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  moving constant text[] := array['status', 'updated_at', 'version'];
begin
  if tg_op = 'INSERT' then
    if new.status <> 'received' then
      perform app.refuse('a new written question starts as received');
    end if;
    if not exists (select 1 from app.written_question_rounds r where r.id = new.round_id and r.case_id = new.case_id) then
      perform app.refuse('a question belongs to a round of its own case');
    end if;
    return new;
  end if;
  if new.case_id <> old.case_id or new.round_id <> old.round_id or new.number <> old.number
     or new.text_verbatim <> old.text_verbatim or new.word_ids <> old.word_ids then
    perform app.refuse('a written question keeps its words');
  end if;
  if new.status <> old.status and (old.status, new.status) not in
     (('received', 'answering'), ('answering', 'answered'), ('received', 'answered'), ('answered', 'served')) then
    perform app.refuse(format('a written question cannot move from %s to %s', old.status, new.status));
  end if;
  if old.status in ('answered', 'served')
     and (to_jsonb(new) - moving) is distinct from (to_jsonb(old) - moving) then
    perform app.refuse('an answered question does not change');
  end if;
  if old.status = 'served' then
    perform app.refuse('a served answer does not change');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.written_questions_guard() from public;
create trigger written_questions_guard before insert or update on app.written_questions
  for each row execute function app.written_questions_guard();

-- The other side's expert report ----------------------------------------------------------------------------
create table app.opposing_reports (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null unique references app.documents (id),
  received_on date not null,
  job_id uuid,
  checked_at timestamptz,
  claims_found integer check (claims_found >= 0),
  positions jsonb not null default '[]' check (jsonb_typeof(positions) = 'array'),
  created_by uuid references app.people (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index opposing_reports_case on app.opposing_reports (case_id);

create table app.opposing_claims (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null references app.documents (id),
  claim_no integer not null check (claim_no > 0),
  paragraph_ref text check (length(paragraph_ref) <= 20),
  text_verbatim bytea not null,
  word_ids text[] not null,
  cited_page_ref text check (length(cited_page_ref) <= 20),
  cited_value bytea,
  status text not null default 'unchecked'
    check (status in ('unchecked', 'matches', 'wrong_page', 'wrong_value', 'not_on_page', 'contradicted')),
  checked_by text check (checked_by in ('code', 'model')),
  evidence_page_id uuid references app.pages (id),
  evidence_word_ids text[] not null default '{}',
  reason text check (length(reason) <= 80),
  expert_confirmed boolean not null default false,
  confirmed_by uuid references app.people (id),
  confirmed_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (document_id, claim_no)
);
create index opposing_claims_case on app.opposing_claims (case_id);

create function app.opposing_claims_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.expert_confirmed then
      perform app.refuse('a claim is confirmed by the expert, not when it is found');
    end if;
    return new;
  end if;
  if new.case_id <> old.case_id or new.document_id <> old.document_id or new.claim_no <> old.claim_no
     or new.text_verbatim <> old.text_verbatim or new.word_ids <> old.word_ids
     or new.cited_page_ref is distinct from old.cited_page_ref or new.cited_value is distinct from old.cited_value then
    perform app.refuse('a claim keeps the words of their report');
  end if;
  if old.status <> 'unchecked' and new.status = 'unchecked' then
    perform app.refuse('a checked claim does not go back to unchecked');
  end if;
  if new.expert_confirmed and new.status = 'unchecked' then
    perform app.refuse('only a checked claim can be confirmed');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger opposing_claims_guard before insert or update on app.opposing_claims
  for each row execute function app.opposing_claims_guard();

-- Changes to a served report --------------------------------------------------------------------------------
create table app.report_changes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  served_version_id uuid not null references app.report_versions (id),
  kind text not null check (kind in ('correction', 'change_of_opinion')),
  paragraph_id uuid not null references app.report_paragraphs (id),
  old_text bytea not null,
  new_text bytea not null,
  reason bytea not null,
  citation_refs jsonb not null default '[]' check (jsonb_typeof(citation_refs) = 'array'),
  dependent_paragraph_ids uuid[] not null default '{}',
  suggested_dependents jsonb not null default '[]' check (jsonb_typeof(suggested_dependents) = 'array'),
  supplementary_version_id uuid references app.report_versions (id),
  version integer not null default 1,
  created_by uuid references app.people (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index report_changes_case on app.report_changes (case_id, created_at);

create function app.report_changes_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if not exists (select 1 from app.report_versions v where v.id = new.served_version_id
                   and v.case_id = new.case_id and v.status = 'served') then
      perform app.refuse('a change is made to a served version of its own case');
    end if;
    if not exists (select 1 from app.report_paragraphs p where p.id = new.paragraph_id
                   and p.report_version_id = new.served_version_id) then
      perform app.refuse('the paragraph belongs to the served version');
    end if;
    if new.supplementary_version_id is not null then
      perform app.refuse('a new change is not yet in a supplementary report');
    end if;
    return new;
  end if;
  if old.supplementary_version_id is not null and exists (
    select 1 from app.report_versions v where v.id = old.supplementary_version_id and v.locked_at is not null
  ) then
    perform app.report_locked('this change is in a signed supplementary report');
  end if;
  if new.case_id <> old.case_id or new.served_version_id <> old.served_version_id
     or new.paragraph_id <> old.paragraph_id or new.created_at <> old.created_at then
    perform app.refuse('a change keeps its paragraph');
  end if;
  if new.supplementary_version_id is not null and not exists (
    select 1 from app.report_versions v where v.id = new.supplementary_version_id and v.case_id = new.case_id
      and v.type = 'supplementary' and v.prev_version_id = new.served_version_id
  ) then
    perform app.refuse('a change goes into a supplementary report on its served version');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.report_changes_guard() from public;
create trigger report_changes_guard before insert or update on app.report_changes
  for each row execute function app.report_changes_guard();

-- New records that may matter -------------------------------------------------------------------------------
create table app.may_matter (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id),
  fact_id uuid not null references app.facts (id),
  question_id uuid not null references app.questions (id),
  why text not null check (length(why) between 1 and 80),
  job_id uuid,
  seen_at timestamptz,
  created_at timestamptz not null default now(),
  unique (fact_id, question_id)
);
create index may_matter_case on app.may_matter (case_id);

-- Access ----------------------------------------------------------------------------------------------------
alter table app.court_directions enable row level security;
alter table app.hearings enable row level security;
alter table app.deadlines enable row level security;
alter table app.deadline_changes enable row level security;
alter table app.written_question_rounds enable row level security;
alter table app.written_questions enable row level security;
alter table app.opposing_reports enable row level security;
alter table app.opposing_claims enable row level security;
alter table app.report_changes enable row level security;
alter table app.may_matter enable row level security;

create policy court_directions_view on app.court_directions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy court_directions_add on app.court_directions for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy hearings_view on app.hearings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy hearings_add on app.hearings for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy hearings_change on app.hearings for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup')) with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy deadlines_view on app.deadlines for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy deadlines_add on app.deadlines for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy deadlines_change on app.deadlines for update to app_api
  using (app.can_access_case(case_id, 'case.edit_setup')) with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy deadline_changes_view on app.deadline_changes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy deadline_changes_add on app.deadline_changes for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));

create policy written_question_rounds_view on app.written_question_rounds for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy written_question_rounds_add on app.written_question_rounds for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy written_question_rounds_change on app.written_question_rounds for update to app_api
  using (app.can_access_case(case_id, 'case.upload') or app.can_access_case(case_id, 'report.sign'))
  with check (app.can_access_case(case_id, 'case.upload') or app.can_access_case(case_id, 'report.sign'));

create policy written_questions_view on app.written_questions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy written_questions_change on app.written_questions for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));

create policy opposing_reports_view on app.opposing_reports for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy opposing_reports_add on app.opposing_reports for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));

create policy opposing_claims_view on app.opposing_claims for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy opposing_claims_change on app.opposing_claims for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));

create policy report_changes_view on app.report_changes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_changes_add on app.report_changes for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy report_changes_change on app.report_changes for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));

create policy may_matter_view on app.may_matter for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));

create policy court_directions_job on app.court_directions for select to app_worker
  using (case_id = app.current_job_case_id());
create policy hearings_job on app.hearings for select to app_worker
  using (case_id = app.current_job_case_id());
create policy deadlines_job on app.deadlines to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy deadline_changes_job on app.deadline_changes to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy written_question_rounds_job on app.written_question_rounds to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy written_questions_job on app.written_questions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy opposing_reports_job on app.opposing_reports to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy opposing_claims_job on app.opposing_claims to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy report_changes_job on app.report_changes to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy may_matter_job on app.may_matter to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

revoke delete on app.court_directions, app.hearings, app.deadlines, app.deadline_changes,
  app.written_question_rounds, app.written_questions, app.opposing_reports, app.opposing_claims,
  app.report_changes, app.may_matter from app_api, app_worker;
revoke update on app.court_directions, app.deadline_changes from app_api, app_worker;

-- Pack deadlines once a report is served ----------------------------------------------------------------------
-- The day the case's report was first served (answers and supplementary reports do not count).
create function app.after_serving_served_on(p_case uuid) returns date
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select min(s.served_on) from app.report_servings s
  join app.report_versions v on v.id = s.report_version_id
  where s.case_id = p_case and v.case_id = p_case and v.type not in ('answers', 'supplementary')
    and (coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case, false) or app.can_access_case(p_case, 'case.view_content'))
$$;
revoke all on function app.after_serving_served_on(uuid) from public;
grant execute on function app.after_serving_served_on(uuid) to app_api, app_worker;

-- When the first serving was recorded (new pages after this are "new since served").
create function app.after_serving_served_at(p_case uuid) returns timestamptz
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select min(s.created_at) from app.report_servings s
  join app.report_versions v on v.id = s.report_version_id
  where s.case_id = p_case and v.case_id = p_case and v.type not in ('answers', 'supplementary')
    and (coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case, false) or app.can_access_case(p_case, 'case.view_content'))
$$;
revoke all on function app.after_serving_served_at(uuid) from public;
grant execute on function app.after_serving_served_at(uuid) to app_api, app_worker;

-- Who made a serving of this version in the last ten minutes, so slice G's `report_served` handler can act
-- as that person (the event names no person, and only the worker role may act as a job). Older servings
-- name nobody, so the function cannot be used to learn who served long ago.
create function app.after_serving_served_by(p_case uuid, p_version uuid) returns uuid
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select s.created_by from app.report_servings s
  where s.case_id = p_case and s.report_version_id = p_version
    and s.created_at > now() - interval '10 minutes'
  order by s.created_at desc
  limit 1
$$;
revoke all on function app.after_serving_served_by(uuid, uuid) from public;
grant execute on function app.after_serving_served_by(uuid, uuid) to app_api;

-- Add a deadline the pack sets from serving, once per kind (the date is worked out in code from the
-- pack). Anyone who can see the case may cause it (the date is the pack's, not theirs), and so may the
-- event handler acting for the case. Returns the deadline id, or null if the case is not served.
create function app.after_serving_pack_deadline(
  p_case uuid, p_kind text, p_rule_ref text, p_due_on date, p_calendar text, p_timezone text, p_pack_version text
) returns uuid
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_id uuid;
begin
  if not coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case
                  or app.can_access_case(p_case, 'case.view_content'), false) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if not exists (select 1 from app.report_servings s join app.report_versions v on v.id = s.report_version_id
                 where s.case_id = p_case and v.type not in ('answers', 'supplementary')) then
    return null;
  end if;
  select d.id into v_id from app.deadlines d where d.case_id = p_case and d.kind = p_kind and d.created_from = 'pack';
  if v_id is not null then
    return v_id;
  end if;
  insert into app.deadlines (case_id, kind, rule_ref, due_on, calendar, timezone, created_from, source, pack_version)
    values (p_case, p_kind, p_rule_ref, p_due_on, p_calendar, p_timezone, 'pack', 'pack', p_pack_version)
    on conflict do nothing
    returning id into v_id;
  if v_id is null then
    select d.id into v_id from app.deadlines d where d.case_id = p_case and d.kind = p_kind and d.created_from = 'pack';
    return v_id;
  end if;
  insert into app.deadline_changes (case_id, deadline_id, old_due_on, new_due_on, reason, source, changed_by)
    values (p_case, v_id, null, p_due_on, 'served', 'pack', app.current_person_id());
  return v_id;
end
$$;
revoke all on function app.after_serving_pack_deadline(uuid, text, text, date, text, text, text) from public;
grant execute on function app.after_serving_pack_deadline(uuid, text, text, date, text, text, text) to app_api, app_worker;

-- When an answers document is served, its round and questions are served too.
create function app.after_serving_answers_served(p_case uuid, p_version uuid) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_count integer := 0;
begin
  if not coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case
                  or app.can_access_case(p_case, 'case.view_content'), false) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if not exists (select 1 from app.report_versions v where v.id = p_version and v.case_id = p_case
                 and v.type = 'answers' and v.status = 'served') then
    return 0;
  end if;
  update app.written_questions q set status = 'served'
    from app.written_question_rounds r
    where r.id = q.round_id and r.answers_version_id = p_version and r.case_id = p_case and q.status = 'answered';
  get diagnostics v_count = row_count;
  update app.written_question_rounds r set status = 'served'
    where r.answers_version_id = p_version and r.case_id = p_case and r.status = 'answered';
  return v_count;
end
$$;
revoke all on function app.after_serving_answers_served(uuid, uuid) from public;
grant execute on function app.after_serving_answers_served(uuid, uuid) to app_api, app_worker;

-- The daily reminder run reads every open deadline due within `p_days` days (dates only; no patient words).
-- Runs as the worker with no case set; it sees only the fields a reminder needs.
create function app.after_serving_due_deadlines(p_run_date date, p_days integer)
  returns table (deadline_id uuid, case_id uuid, organisation_id uuid, named_expert_id uuid, kind text,
                 due_on date, last_reminded_on date)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select d.id, d.case_id, c.organisation_id, c.named_expert_id, d.kind, d.due_on, d.last_reminded_on
  from app.deadlines d join app.cases c on c.id = d.case_id
  where d.done_at is null and d.due_on between p_run_date and p_run_date + p_days
    and c.status not in ('settled', 'withdrawn', 'archived', 'deleted')
  order by d.due_on, d.id
$$;
revoke all on function app.after_serving_due_deadlines(date, integer) from public, app_api;
grant execute on function app.after_serving_due_deadlines(date, integer) to app_worker;


-- The case's pack codes, for the report-served handler, which acts for the case (not a person) and so
-- cannot read the cases table.
create function app.after_serving_case_packs(p_case uuid)
  returns table (jurisdiction text, case_type text, specialty text)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select c.jurisdiction, c.case_type, c.specialty from app.cases c
  where c.id = p_case
    and (coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case, false) or app.can_access_case(p_case, 'case.view_content'))
$$;
revoke all on function app.after_serving_case_packs(uuid) from public;
grant execute on function app.after_serving_case_packs(uuid) to app_api, app_worker;

reset role;
