set role app_migrator;

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
