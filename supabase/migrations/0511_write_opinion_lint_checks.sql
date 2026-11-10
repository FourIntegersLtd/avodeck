-- 0511 write (slice E): whether the model check ran on the saved opinion text (audit step 14, W-E2).
-- A check the model was asked to make and could not answer (its answer failed twice) is "not run" and is
-- never shown as passed. That state used to be read from the audit trail, which shows a person only their
-- own rows, so a case member who was not the named expert saw every check as passed. It is kept here
-- instead, where everyone who may view the opinion can read it. One row per opinion, for the opinion
-- version it was made on: a later save moves the version on, so a new text is "not asked yet" again.
-- No case_id column: the row follows its opinion (deleted with it) and access is decided through it.

set role app_migrator;

create table app.opinion_lint_checks (
  opinion_id uuid primary key references app.opinions (id) on delete cascade,
  opinion_version integer not null check (opinion_version > 0),
  model_answered boolean not null,
  checked_at timestamptz not null default now()
);

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.opinion_lint_checks enable row level security;

create policy opinion_lint_checks_view on app.opinion_lint_checks for select to app_api
  using (exists (
    select 1 from app.opinions o
    where o.id = opinion_id and app.can_access_case(o.case_id, 'case.view_content')
  ));
create policy opinion_lint_checks_add on app.opinion_lint_checks for insert to app_api
  with check (exists (
    select 1 from app.opinions o
    where o.id = opinion_id and app.can_access_case(o.case_id, 'opinion.write')
  ));
create policy opinion_lint_checks_change on app.opinion_lint_checks for update to app_api
  using (exists (
    select 1 from app.opinions o
    where o.id = opinion_id and app.can_access_case(o.case_id, 'opinion.write')
  ))
  with check (exists (
    select 1 from app.opinions o
    where o.id = opinion_id and app.can_access_case(o.case_id, 'opinion.write')
  ));

reset role;
