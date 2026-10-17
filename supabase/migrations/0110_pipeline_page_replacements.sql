-- 0110 pipeline: clearer copies of pages (S6, architecture 10.3 "Clearer copies").
--
-- One row per page the expert or their assistant replaces with a clearer copy uploaded as later
-- records. Once the copy's document has been cut into pages, `new_page_id` is its first page and the
-- old page is superseded by it (`pages.superseded_by_page_id`). A page is replaced once and a
-- document replaces one page. Rows are never removed; only `new_page_id` is set, once.

set role app_migrator;

create table app.page_replacements (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null unique references app.pages (id) on delete cascade,
  document_id uuid not null unique references app.documents (id) on delete cascade,
  new_page_id uuid references app.pages (id) on delete cascade,
  requested_by uuid not null references app.people (id),
  linked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index page_replacements_case on app.page_replacements (case_id);

create function app.page_replacements_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id or new.document_id <> old.document_id
     or new.requested_by <> old.requested_by then
    perform app.refuse('a replacement stays on its page and copy');
  end if;
  if old.new_page_id is not null and new.new_page_id is distinct from old.new_page_id then
    perform app.refuse('a replaced page stays replaced');
  end if;
  new.updated_at := now();
  return new;
end
$$;

create trigger page_replacements_guard before update on app.page_replacements
  for each row execute function app.page_replacements_guard();

alter table app.page_replacements enable row level security;
grant select, insert, update on app.page_replacements to app_api;
grant select, update on app.page_replacements to app_worker;
revoke delete, truncate on app.page_replacements from app_api, app_worker;

create policy page_replacements_view on app.page_replacements for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_replacements_ask on app.page_replacements for insert to app_api
  with check (app.can_access_case(case_id, 'page.replace') and requested_by = app.current_person_id());
create policy page_replacements_link on app.page_replacements for update to app_api
  using (app.can_access_case(case_id, 'page.replace')) with check (app.can_access_case(case_id, 'page.replace'));
create policy page_replacements_job on app.page_replacements to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
