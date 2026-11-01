-- 0041 foundation: bundle-level access function and constraint migration.
--
-- `can_access_bundle` is the single entry point for all page-level RLS policies. It checks
-- whether the current person has access to any non-deleted case that references the bundle.
--
-- The unique constraint on pages moves from (case_id, bundle_page_no) to (bundle_id, bundle_page_no).
-- The pages_guard trigger is updated to also refuse changes to bundle_id.

set role app_migrator;

-- 1. Access function ---------------------------------------------------------------------------
create function app.can_access_bundle(p_bundle_id uuid, p_action text)
returns boolean
language sql stable security definer
set search_path = app
as $$
  select exists (
    select 1 from app.cases c
    where c.bundle_id = p_bundle_id
      and c.status <> 'deleted'
      and app.can_access_case(c.id, p_action)
  )
$$;

-- 2. Move unique constraint on pages -----------------------------------------------------------
alter table app.pages drop constraint pages_case_id_bundle_page_no_key;
alter table app.pages add constraint pages_bundle_page_no_key unique (bundle_id, bundle_page_no);

-- 3. Update pages_guard: also refuse bundle_id changes ----------------------------------------
-- The existing trigger (0002) refuses bundle_page_no changes. We replace it to also guard bundle_id.
create or replace function app.pages_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'UPDATE' then
    if new.bundle_page_no is distinct from old.bundle_page_no then
      raise exception 'a page number never changes'
        using errcode = 'check_violation';
    end if;
    if new.bundle_id is distinct from old.bundle_id then
      raise exception 'a page cannot move between bundles'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;

reset role;
