-- 0012 foundation: each sensitive column of a case needs its own action (rule 9, permissions.md).
-- `cases_change` lets `case.edit_setup` (named expert, assistant) or `case.manage_members`
-- (named expert, organisation owner/admin) update a case. The database now checks each change
-- against the action it is, for the person the restricted role acts for:
--   named_expert_id                          case.reassign_named_expert, and only before serving
--   retention_policy, delete_after, on_hold  case.retention
--   status → deleted                         case.delete_now, and only for an unserved case
--   status → withdrawn                       case.withdraw
--   status → served                          report.serve
--   any other status move or column          case.edit_setup
-- "Served" means the case is or was served: status served, or a served or withdrawn report version.
-- The worker (app_worker) acts for its own case's job (processing, the deletion job) and is not
-- checked here. `cases_guard` still allows only the moves in state-machines.md.

set role app_migrator;

create function app.case_ever_served(p_case_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.cases c where c.id = p_case_id and c.status in ('served', 'settled'))
      or exists (select 1 from app.report_versions r
                 where r.case_id = p_case_id and r.status in ('served', 'withdrawn'))
$$;

create function app.cases_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  own constant text[] := array['named_expert_id', 'retention_policy', 'delete_after', 'on_hold', 'status',
                               'version', 'updated_at'];
  needed text[] := '{}';
  action text;
begin
  if current_user <> 'app_api' then
    return new;
  end if;
  if new.named_expert_id is distinct from old.named_expert_id then
    if app.case_ever_served(old.id) then
      raise exception using errcode = '42501', message = 'the named expert cannot change after serving';
    end if;
    needed := array_append(needed, 'case.reassign_named_expert');
  end if;
  if (new.retention_policy, new.delete_after, new.on_hold)
     is distinct from (old.retention_policy, old.delete_after, old.on_hold) then
    needed := array_append(needed, 'case.retention');
  end if;
  if new.status is distinct from old.status then
    if new.status = 'deleted' and app.case_ever_served(old.id) then
      raise exception using errcode = '42501', message = 'a served case is deleted only by its retention';
    end if;
    needed := array_append(needed, case new.status
      when 'deleted' then 'case.delete_now'
      when 'withdrawn' then 'case.withdraw'
      when 'served' then 'report.serve'
      else 'case.edit_setup' end);
  end if;
  if (to_jsonb(new) - own) is distinct from (to_jsonb(old) - own) then
    needed := array_append(needed, 'case.edit_setup');
  end if;
  foreach action in array needed loop
    if not app.can_access_case(old.id, action) then
      raise exception using errcode = '42501', message = 'this change needs ' || action;
    end if;
  end loop;
  return new;
end
$$;

revoke execute on function app.case_ever_served(uuid) from public;
grant execute on function app.case_ever_served(uuid) to app_api, app_worker;

-- Named to run before cases_guard (triggers fire in name order).
create trigger cases_actor before update on app.cases
  for each row execute function app.cases_actor();

reset role;
