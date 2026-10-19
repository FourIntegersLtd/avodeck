-- 0901 trial: what the expert prepares for the day. A reply to each readiness question (screen 24), the
-- sentences of a new note found in no served document with the expert's choice about each (screen 24), when
-- the expert told the instructing party about missing pages (screen 23), and the pages each section of a
-- built trial pack took.
--
-- The reply and the note are the expert's own words, so they are sealed under the case key like the agenda
-- positions. The two new tables join the purge order (app.case_purge_order(), migration 0017), just before
-- `concordance`, in the same order as `PURGE_ORDER` in api/app/core/contracts/case_tables.py.

set role app_migrator;

create table app.readiness_replies (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_no integer not null check (question_no >= 1),
  reply bytea not null,
  replied_at timestamptz not null default now(),
  replied_by uuid default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, question_no)
);

create table app.trial_notes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  text_sha256 text not null check (text_sha256 ~ '^[0-9a-f]{64}$'),
  text bytea not null,
  choice text check (choice in ('tell', 'leave_out')),
  decided_at timestamptz,
  decided_by uuid,
  created_at timestamptz not null default now(),
  unique (case_id, text_sha256),
  check ((choice is null) = (decided_at is null))
);

alter table app.trial_packs add column section_pages jsonb not null default '{}';
alter table app.court_bundles add column missing_told_at timestamptz;

create function app.trial_reply_touch() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger readiness_replies_touch before update on app.readiness_replies
  for each row execute function app.trial_reply_touch();

alter table app.readiness_replies enable row level security;
alter table app.trial_notes enable row level security;

create policy readiness_replies_view on app.readiness_replies for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy readiness_replies_add on app.readiness_replies for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy readiness_replies_change on app.readiness_replies for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));
create policy readiness_replies_job on app.readiness_replies for select to app_worker
  using (case_id = app.current_job_case_id());

create policy trial_notes_view on app.trial_notes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy trial_notes_add on app.trial_notes for insert to app_api
  with check (app.can_access_case(case_id, 'report.build'));
create policy trial_notes_change on app.trial_notes for update to app_api
  using (app.can_access_case(case_id, 'report.build')) with check (app.can_access_case(case_id, 'report.build'));

revoke delete on app.readiness_replies, app.trial_notes from app_api, app_worker;
revoke insert, update on app.readiness_replies from app_worker;
revoke all on app.trial_notes from app_worker;

do $do$
declare
  current_order text[] := app.case_purge_order();
  added text[] := array['readiness_replies', 'trial_notes'];
  at integer;
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> all (added);
  at := coalesce(array_position(current_order, 'concordance'), array_position(current_order, 'jobs'));
  if at is null then
    raise exception 'neither concordance nor jobs is in the purge order';
  end if;
  spliced := current_order[1:at - 1] || added || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
