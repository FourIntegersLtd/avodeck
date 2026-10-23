-- Undo 0209: facts_guard as in 0208; no `reset` action (reset rows must be gone first).
set role app_migrator;

alter table app.fact_verifications drop constraint fact_verifications_person;
alter table app.fact_verifications add constraint fact_verifications_person
  check (person_id is not null or action = 'remapped');
alter table app.fact_verifications drop constraint fact_verifications_action_check;
alter table app.fact_verifications add constraint fact_verifications_action_check
  check (action in ('correct', 'not_right', 'edit', 'move', 'remapped'));

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
  ) and not (
    -- "Please check" after a re-read: the expert changes the fact to cite the words there now.
    (old.status, new.status) = ('verified', 'edited')
    and exists (select 1 from app.fact_flags g where g.fact_id = new.id and g.flagged_for = 'recheck')
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
