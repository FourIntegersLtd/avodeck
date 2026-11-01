-- 0040 foundation: introduce the bundle as a first-class entity (ADR 0034).
--
-- A bundle is the numbered set of pages that one or more cases share. Before this migration
-- pages and documents reference only the case; after it every page-level table references
-- the bundle, and cases point at their bundle via `cases.bundle_id`.
--
-- This migration creates the table, adds columns, backfills one bundle per existing case,
-- makes the FK non-null, and adds the auto-create trigger for new cases.

set role app_migrator;

-- 1. The bundles table -------------------------------------------------------------------------
create table app.bundles (
  id uuid primary key default gen_random_uuid(),
  -- Lineage only: which case created this bundle. Not FK-enforced — a bundle outlives the case
  -- that created it (purge never deletes a bundle, ADR 0034), and an enforced FK would make
  -- bundles and cases reference each other in a cycle no purge order could satisfy.
  source_case_id uuid not null,
  page_count integer not null default 0,
  created_at timestamptz not null default now()
);
comment on table app.bundles is 'A numbered set of pages shared by one or more cases.';
create index bundles_source_case on app.bundles (source_case_id);

-- 2. Add bundle_id columns (nullable for the backfill step) -----------------------------------
alter table app.cases add column bundle_id uuid references app.bundles (id);
alter table app.documents add column bundle_id uuid references app.bundles (id);
alter table app.pages add column bundle_id uuid references app.bundles (id);

-- 3. Backfill: one bundle per existing case ----------------------------------------------------
insert into app.bundles (id, source_case_id, page_count)
  select gen_random_uuid(), c.id,
    coalesce((select max(p.bundle_page_no) from app.pages p where p.case_id = c.id), 0)
  from app.cases c;

update app.cases c
  set bundle_id = b.id
  from app.bundles b
  where b.source_case_id = c.id;

update app.documents d
  set bundle_id = (select c.bundle_id from app.cases c where c.id = d.case_id)
  where d.bundle_id is null;

update app.pages p
  set bundle_id = (select c.bundle_id from app.cases c where c.id = p.case_id)
  where p.bundle_id is null;

-- 4. Make bundle_id NOT NULL -------------------------------------------------------------------
alter table app.cases alter column bundle_id set not null;
alter table app.documents alter column bundle_id set not null;
alter table app.pages alter column bundle_id set not null;

-- 5. Indexes -----------------------------------------------------------------------------------
create index cases_bundle on app.cases (bundle_id);
create index documents_bundle on app.documents (bundle_id);
create index pages_bundle on app.pages (bundle_id);

-- 6. Auto-create a bundle when a case is inserted ---------------------------------------------
create function app.cases_create_bundle() returns trigger
language plpgsql as $$
declare
  v_bundle_id uuid;
begin
  if new.bundle_id is null then
    -- No `returning ... into` here: the case row doesn't exist yet (this runs before insert),
    -- so the new bundle would fail the bundles_view select policy and RETURNING would raise
    -- "new row violates row-level security policy" even though the insert itself is allowed.
    v_bundle_id := gen_random_uuid();
    insert into app.bundles (id, source_case_id) values (v_bundle_id, new.id);
    new.bundle_id := v_bundle_id;
  end if;
  return new;
end
$$;

create trigger cases_auto_bundle before insert on app.cases
  for each row execute function app.cases_create_bundle();

-- 7. Guard: refuse delete on a bundle while any case references it ----------------------------
create function app.bundles_guard() returns trigger
language plpgsql as $$
begin
  if exists (select 1 from app.cases c where c.bundle_id = old.id) then
    raise exception 'bundle still referenced by a case'
      using errcode = 'foreign_key_violation';
  end if;
  return old;
end
$$;

create trigger bundles_no_orphan_delete before delete on app.bundles
  for each row execute function app.bundles_guard();

-- 8. RLS on bundles ---------------------------------------------------------------------------
alter table app.bundles enable row level security;
revoke delete on app.bundles from app_api;

create policy bundles_view on app.bundles for select to app_api
  using (exists (
    select 1 from app.cases c
    where c.bundle_id = bundles.id
      and app.can_access_case(c.id, 'case.view')
  ));

create policy bundles_insert on app.bundles for insert to app_api
  with check (true);  -- created by the trigger during case insert

create policy bundles_job on app.bundles to app_worker
  using (exists (
    select 1 from app.cases c
    where c.bundle_id = bundles.id
      and c.id = app.current_job_case_id()
  ))
  with check (exists (
    select 1 from app.cases c
    where c.bundle_id = bundles.id
      and c.id = app.current_job_case_id()
  ));

reset role;
