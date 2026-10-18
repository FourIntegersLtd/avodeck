-- 0201 down: facts stay on their page again; no clearer-copy request flag.

set role app_migrator;

create or replace function app.facts_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new fact starts suggested');
    end if;
    if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
      perform app.refuse('a fact cites a page of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.page_id is distinct from old.page_id then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified')
  ) then
    perform app.refuse(format('fact status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

alter table app.pages drop column needs_clearer_copy;

reset role;
