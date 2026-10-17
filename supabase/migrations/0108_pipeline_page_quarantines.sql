-- 0108 pipeline: pages that are not this person's record (screen 30, F25, architecture 10.3).
--
-- One row per suggestion or quarantine of a page. The pipeline (suggested_by null) or an assistant
-- suggests; only the named expert (fact.verify) confirms, dismisses or undoes. A confirmed row keeps
-- the statuses the page's facts had before they were rejected (`prior_facts`, ids and statuses only),
-- so undoing restores verified facts. No identifier is stored: `reason` is a short code.
-- States: suggested -> confirmed | dismissed; confirmed -> undone | purged (images and text deleted).

set role app_migrator;

create table app.page_quarantines (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  status text not null default 'suggested'
    check (status in ('suggested', 'confirmed', 'dismissed', 'undone', 'purged')),
  reason text not null check (reason ~ '^[a-z_]{1,40}$'),
  suggested_by uuid references app.people (id),
  confirmed_by uuid references app.people (id),
  confirmed_at timestamptz,
  closed_by uuid references app.people (id),
  closed_at timestamptz,
  purged_at timestamptz,
  prior_facts jsonb not null default '[]',
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index page_quarantines_live on app.page_quarantines (page_id) where status in ('suggested', 'confirmed');
create index page_quarantines_case on app.page_quarantines (case_id, status);

create function app.page_quarantines_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id then
    perform app.refuse('a quarantine stays on its page');
  end if;
  if new.status <> old.status and not (
    (old.status = 'suggested' and new.status in ('confirmed', 'dismissed'))
    or (old.status = 'confirmed' and new.status in ('undone', 'purged'))
  ) then
    perform app.refuse('this quarantine change is not allowed');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create trigger page_quarantines_guard before update on app.page_quarantines
  for each row execute function app.page_quarantines_guard();

alter table app.page_quarantines enable row level security;
grant select, insert, update on app.page_quarantines to app_api;
grant select, insert on app.page_quarantines to app_worker;
revoke delete, truncate on app.page_quarantines from app_api, app_worker;

create policy page_quarantines_view on app.page_quarantines for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_quarantines_suggest on app.page_quarantines for insert to app_api
  with check (app.can_access_case(case_id, 'page.quarantine') and status = 'suggested'
              and suggested_by = app.current_person_id());
create policy page_quarantines_decide on app.page_quarantines for update to app_api
  using (app.can_access_case(case_id, 'page.quarantine') and app.can_access_case(case_id, 'fact.verify'))
  with check (app.can_access_case(case_id, 'page.quarantine') and app.can_access_case(case_id, 'fact.verify'));
create policy page_quarantines_job_view on app.page_quarantines for select to app_worker
  using (case_id = app.current_job_case_id());
create policy page_quarantines_job_suggest on app.page_quarantines for insert to app_worker
  with check (case_id = app.current_job_case_id() and status = 'suggested' and suggested_by is null);

reset role;
