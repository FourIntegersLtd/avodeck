-- Down 0130: restore missing_items_guard's direct case_id check on app.pages.
-- Run after 0044's down has restored app.pages.case_id (foundation, ADR 0034).

set role app_migrator;

create or replace function app.missing_items_guard() returns trigger
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

reset role;
