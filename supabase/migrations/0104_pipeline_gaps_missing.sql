-- 0104 pipeline: quiet record sources and documents mentioned but not in the bundle (architecture 7,
-- planted items 2 and 3, screens S08 and S08b, state-machines "Missing item").
-- * `record_gaps`: one row per source and series of one fact kind that stops before the source's
--   last month. Recalculated by the worker; a row the expert has reviewed (`confirmed` set) is kept.
-- * `missing_items`: one row per reference on a page (T-07c) that no page of the case holds. The
--   description and any reason are sealed per case; statuses move only along the state machine.
-- * `page_analysis.references_checked_at`: when the page's references were matched, so a repeated
--   job asks nothing again.
-- The API may change only the review columns of `missing_items`, with the request action on the case.

set role app_migrator;

create table app.record_gaps (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  source_type text not null,
  fact_kind text not null,
  gap_start date not null,
  gap_end date not null check (gap_end >= gap_start),
  last_page_id uuid references app.pages (id) on delete set null,
  confirmed boolean,
  confirmed_by uuid references app.people (id),
  confirmed_at timestamptz,
  version integer not null default 0 check (version >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, source_type, fact_kind)
);

create table app.missing_items (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  citing_page_id uuid not null references app.pages (id) on delete cascade,
  reference_key text not null check (reference_key ~ '^r[0-9]+$'),
  word_ids text[] not null check (cardinality(word_ids) > 0),
  description bytea not null,
  reference_date date,
  confidence real check (confidence between 0 and 1),
  status text not null default 'open' check (status in ('open', 'requested', 'received', 'not_available')),
  reason bytea,
  received_page_id uuid references app.pages (id) on delete set null,
  updated_by uuid references app.people (id),
  version integer not null default 0 check (version >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (citing_page_id, reference_key),
  check (status <> 'not_available' or reason is not null),
  check (status <> 'received' or received_page_id is not null)
);
create index missing_items_case on app.missing_items (case_id);

alter table app.page_analysis add column references_checked_at timestamptz;

create function app.missing_item_status_allowed(from_status text, to_status text) returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select from_status = to_status or (from_status, to_status) in (
    ('open', 'requested'),
    ('requested', 'received'),
    ('open', 'not_available'),
    ('requested', 'not_available'),
    ('not_available', 'open')
  )
$$;

create function app.missing_items_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.pages p where p.id = new.citing_page_id and p.case_id = new.case_id) then
    raise exception 'page belongs to another case' using errcode = 'check_violation';
  end if;
  if tg_op = 'INSERT' then
    if new.status <> 'open' then
      perform app.refuse('a missing item starts open');
    end if;
  elsif not app.missing_item_status_allowed(old.status, new.status) then
    perform app.refuse('missing item status change not allowed');
  end if;
  return new;
end
$$;

create trigger missing_items_guard before insert or update on app.missing_items
  for each row execute function app.missing_items_guard();

alter table app.record_gaps enable row level security;
alter table app.missing_items enable row level security;
revoke insert, update, delete on app.record_gaps, app.missing_items from app_api;
grant update (status, reason, received_page_id, updated_by, version, updated_at) on app.missing_items to app_api;

create policy record_gaps_view on app.record_gaps for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy record_gaps_job on app.record_gaps to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy missing_items_view on app.missing_items for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy missing_items_review on app.missing_items for update to app_api
  using (app.can_access_case(case_id, 'request.draft')) with check (app.can_access_case(case_id, 'request.draft'));
create policy missing_items_job on app.missing_items to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
