-- 0370 setup (slice C): conflict answers keep their history (review finding 7, screen 05).
-- * conflict_answer_history: a copy of each conflict answer every time it is given or changed (the detail
--   stays encrypted under the case key), written only by the trigger below. Append-only: nobody inserts,
--   changes or deletes a row directly. The answers behind a declaration are the latest history rows for
--   each question recorded before the declaration's `created_at`; a row recorded after the latest
--   declaration means the declaration is out of date (screen 05 asks the expert to declare again).

set role app_migrator;

create table app.conflict_answer_history (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_key text not null check (question_key ~ '^[a-z][a-z0-9_]{0,39}$'),
  answer text not null check (answer in ('yes', 'no')),
  detail bytea,
  answered_by uuid references app.people (id),
  recorded_at timestamptz not null default clock_timestamp()
);
create index conflict_answer_history_case on app.conflict_answer_history (case_id, recorded_at);

revoke insert, update, delete on app.conflict_answer_history from app_api, app_worker;

create function app.conflict_answers_keep_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.conflict_answer_history (case_id, question_key, answer, detail, answered_by)
  values (new.case_id, new.question_key, new.answer, new.detail, new.answered_by);
  return null;
end
$$;
revoke all on function app.conflict_answers_keep_history() from public;

create trigger conflict_answers_keep_history after insert or update on app.conflict_answers
  for each row execute function app.conflict_answers_keep_history();

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.conflict_answer_history enable row level security;

create policy conflict_answer_history_view on app.conflict_answer_history for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));

reset role;
