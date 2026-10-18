-- 0201 reader: clearer copies (S10, architecture 10).
--
-- * `pages.needs_clearer_copy`: the expert asked for a clearer copy of this page; slice A's
--   instructing-party request draft lists it, as it lists unreadable pages.
-- * `facts_guard` [foundation path, approved for slice B]: a fact may move to the page that
--   supersedes its own page (the clearer copy) and to no other page. Status is kept; `version`
--   rises as on every change. Keeps 0023's exception for `app.set_fact_availability()`.

set role app_migrator;

alter table app.pages add column needs_clearer_copy boolean not null default false;

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
  if new.case_id is distinct from old.case_id then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.page_id is distinct from old.page_id and not exists (
    select 1 from app.pages p where p.id = old.page_id and p.superseded_by_page_id = new.page_id
  ) then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified')
  ) then
    perform app.refuse(format('fact status cannot change from %s to %s', old.status, new.status));
  end if;
  -- app.set_fact_availability() alone may change when an entry reached the clinician without a new version.
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
