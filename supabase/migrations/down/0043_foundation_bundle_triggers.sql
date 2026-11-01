-- Undo 0043: restore facts_guard to its 0023 body (plain pages.case_id check, no bundle join).
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
    if not exists (
      select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id
    ) then
      perform app.refuse('a fact cites a page of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.page_id is distinct from old.page_id and not exists (
    select 1 from app.pages p where p.id = old.page_id and p.superseded_by_page_id = new.page_id
  ) then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.status = 'suggested' and old.status <> 'suggested'
     and current_setting('app.fact_checks_reset', true) = new.case_id::text and pg_trigger_depth() > 1 then
    new.version := old.version + 1;
    new.updated_at := now();
    return new;
  end if;
  if new.status is distinct from old.status and new.status <> 'suggested' then
    if (select c.named_expert_id from app.cases c where c.id = new.case_id for share)
         is distinct from app.current_person_id()
       and session_user = 'app_api' then
      raise exception using errcode = '42501', message = 'only the case''s named expert checks its facts';
    end if;
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified'), ('verified', 'edited')
  ) then
    perform app.refuse(format('fact status cannot change from %s to %s', old.status, new.status));
  end if;
  if current_setting('app.fact_availability_write', true) = new.id::text
     and (to_jsonb(new) - '{available_at,availability_basis}'::text[])
         = (to_jsonb(old) - '{available_at,availability_basis}'::text[]) then
    return new;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
