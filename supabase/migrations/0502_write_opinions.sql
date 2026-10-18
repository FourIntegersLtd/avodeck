-- 0502 write (slice E): the expert's opinion on each question, in their own words (rule 1, screen 15).
-- * opinions: one per question. `body` is the expert's text (encrypted under the case key) with citation
--   markers that map one-to-one onto `citation_refs` (each a verified fact, its page and the exact words
--   quoted, checked by the API before saving). `view` is a code from the case-type pack's opinion views;
--   `provisional` marks an opinion awaiting records. Only the named expert writes (`opinion.write`).
-- * opinion_versions: every save, kept for ever. Written only by the history trigger below; nobody
--   changes or removes a version (the case purge removes them with the case).

set role app_migrator;

create table app.opinions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  question_id uuid not null unique references app.questions (id) on delete cascade,
  body bytea not null,
  citation_refs jsonb not null default '[]' check (jsonb_typeof(citation_refs) = 'array'),
  view text check (view ~ '^[a-z][a-z0-9_]*$'),
  provisional boolean not null default false,
  restored_from integer check (restored_from > 0),
  updated_by uuid,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index opinions_case on app.opinions (case_id);

create function app.opinions_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.questions q where q.id = new.question_id and q.case_id = new.case_id) then
    perform app.refuse('an opinion answers a question of its own case');
  end if;
  new.updated_by := app.current_person_id();
  if tg_op = 'INSERT' then
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.question_id is distinct from old.question_id then
    perform app.refuse('an opinion stays with its question');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.opinions_guard() from public;
create trigger opinions_guard before insert or update on app.opinions
  for each row execute function app.opinions_guard();

create table app.opinion_versions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  opinion_id uuid not null references app.opinions (id) on delete cascade,
  version_no integer not null check (version_no > 0),
  body bytea not null,
  citation_refs jsonb not null default '[]',
  view text,
  provisional boolean not null default false,
  restored_from integer,
  saved_by uuid,
  created_at timestamptz not null default now(),
  unique (opinion_id, version_no)
);
create index opinion_versions_case on app.opinion_versions (case_id);

create function app.opinions_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.opinion_versions
    (case_id, opinion_id, version_no, body, citation_refs, view, provisional, restored_from, saved_by)
  values
    (new.case_id, new.id, new.version, new.body, new.citation_refs, new.view, new.provisional, new.restored_from,
     new.updated_by);
  return null;
end
$$;
revoke all on function app.opinions_history() from public;
create trigger opinions_history after insert or update on app.opinions
  for each row execute function app.opinions_history();

revoke delete on app.opinions from app_api, app_worker;
revoke insert, update, delete on app.opinion_versions from app_api, app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.opinions enable row level security;
alter table app.opinion_versions enable row level security;

create policy opinions_view on app.opinions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy opinions_add on app.opinions for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy opinions_change on app.opinions for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy opinion_versions_view on app.opinion_versions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));

reset role;
