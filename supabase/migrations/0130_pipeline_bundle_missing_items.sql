-- 0130 pipeline: fix missing_items_guard for the bundle split.
--
-- app.pages lost its case_id column in 0044 (ADR 0034). The "page belongs to another case" check
-- now resolves the citing page's case through its bundle instead.

set role app_migrator;

create or replace function app.missing_items_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if not exists (
    select 1 from app.pages p
      join app.cases c on c.bundle_id = p.bundle_id
    where p.id = new.citing_page_id and c.id = new.case_id
  ) then
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

reset role;
