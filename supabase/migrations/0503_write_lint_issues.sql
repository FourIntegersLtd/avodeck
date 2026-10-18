-- 0503 write (slice E): wording to check in an opinion (screen 15). Found by the pattern check on every
-- save (`regex`: a date or number with no citation, a loaded word from the pack) or by T-15a (`ai`:
-- e.g. a sentence outside the expert's field). Each issue points at a sentence and quotes the words
-- flagged (encrypted); it never offers replacement text (rule 1). Open issues are recalculated on save
-- (deleted and found again); a dismissal needs the expert's reason and is kept.

set role app_migrator;

create table app.lint_issues (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  opinion_id uuid references app.opinions (id) on delete cascade,
  paragraph_id uuid,
  opinion_version integer not null check (opinion_version > 0),
  sentence_index integer not null check (sentence_index >= 0),
  type text not null check (type ~ '^[a-z][a-z0-9_]*$'),
  words bytea not null,
  source text not null check (source in ('regex', 'ai')),
  status text not null default 'open' check (status in ('open', 'dismissed')),
  dismissed_reason bytea,
  dismissed_by uuid,
  dismissed_at timestamptz,
  created_at timestamptz not null default now(),
  check (opinion_id is not null or paragraph_id is not null),
  check (status <> 'dismissed' or dismissed_reason is not null)
);
create index lint_issues_opinion on app.lint_issues (opinion_id, status);
create index lint_issues_case on app.lint_issues (case_id);

create function app.lint_issues_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'open' then
      perform app.refuse('a new issue starts open');
    end if;
    if new.opinion_id is not null
       and not exists (select 1 from app.opinions o where o.id = new.opinion_id and o.case_id = new.case_id) then
      perform app.refuse('an issue belongs to an opinion of its own case');
    end if;
    return new;
  end if;
  if old.status <> 'open' or new.status <> 'dismissed'
     or (new.case_id, new.opinion_id, new.paragraph_id, new.opinion_version, new.sentence_index, new.type,
         new.words, new.source)
        is distinct from
        (old.case_id, old.opinion_id, old.paragraph_id, old.opinion_version, old.sentence_index, old.type,
         old.words, old.source) then
    perform app.refuse('an open issue can only be dismissed, and a dismissal is kept');
  end if;
  new.dismissed_by := app.current_person_id();
  new.dismissed_at := now();
  return new;
end
$$;
revoke all on function app.lint_issues_guard() from public;
create trigger lint_issues_guard before insert or update on app.lint_issues
  for each row execute function app.lint_issues_guard();

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.lint_issues enable row level security;

create policy lint_issues_view on app.lint_issues for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy lint_issues_add on app.lint_issues for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy lint_issues_dismiss on app.lint_issues for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy lint_issues_recalculate on app.lint_issues for delete to app_api
  using (status = 'open' and app.can_access_case(case_id, 'opinion.write'));
create policy lint_issues_job on app.lint_issues to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
