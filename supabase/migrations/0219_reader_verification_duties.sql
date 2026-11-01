-- 0219 reader: role-conditional fact verification (Decision B).
--
-- `facts_guard` (0212) hard-codes `cases.named_expert_id` as the only person who may change a
-- fact's status. The pack may name other case roles with verification duty (e.g. a future
-- co_expert pack). To avoid a per-row pack lookup in the trigger, the duty roles are stored on
-- the case itself, set from the pack at case creation.
--
-- `fact.verify` in the `case_action_allowed` matrix (0200) stays `{named_expert}` for now — that
-- is the static permission check; this trigger is the dynamic, pack-driven check. When a future
-- pack adds a role to verification_duties, both the matrix and verification_duty_roles widen.

set role app_migrator;

alter table app.cases add column verification_duty_roles text[] not null default '{named_expert}';

-- Redefine facts_guard: the named-expert-only check becomes a role-membership check against
-- the case's verification_duty_roles, covering the named expert and any case_members role.
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
      select 1 from app.pages p
        join app.cases c on c.bundle_id = p.bundle_id
      where p.id = new.page_id and c.id = new.case_id
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
  -- Reassignment (0031): only from inside cases_named_expert_reset, for the case it names.
  if new.status = 'suggested' and old.status <> 'suggested'
     and current_setting('app.fact_checks_reset', true) = new.case_id::text and pg_trigger_depth() > 1 then
    new.version := old.version + 1;
    new.updated_at := now();
    return new;
  end if;
  -- A check and a reassignment never cross (0031): hold the case row while the check is made.
  -- The person must hold a role named in the case's verification_duty_roles (default: named_expert
  -- only, matching every launch pack exactly). named_expert always carries verification duty
  -- (enforced by the pack loader), so this is never a way to turn verification off.
  if new.status is distinct from old.status and new.status <> 'suggested' then
    perform 1 from app.cases c where c.id = new.case_id for share;
    if session_user = 'app_api' and not exists (
      select 1 from (
        select c.named_expert_id as pid, 'named_expert'::text as role
          from app.cases c where c.id = new.case_id
        union all
        select cm.person_id, cm.case_role
          from app.case_members cm where cm.case_id = new.case_id and cm.removed_at is null
      ) people
      where people.pid = app.current_person_id()
        and (select c2.verification_duty_roles from app.cases c2 where c2.id = new.case_id) @> array[people.role]
    ) then
      raise exception using errcode = '42501', message = 'only a role with verification duty checks facts';
    end if;
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified'), ('verified', 'edited')
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
