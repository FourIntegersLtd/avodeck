-- Down for 0017 foundation: slice C's contracts. Restores the 0003, 0008, 0010 and 0012 definitions.
set role app_migrator;

create or replace function app.questions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new question starts suggested');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.text_verbatim is distinct from old.text_verbatim then
    perform app.refuse('a question keeps its exact words');
  end if;
  if new.status is distinct from old.status
     and (old.status, new.status) not in (('suggested', 'confirmed'), ('suggested', 'edited')) then
    perform app.refuse(format('question status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

alter table app.questions drop constraint if exists questions_instruction_version_fk;
drop index if exists app.questions_instruction_version;
drop table if exists app.instruction_versions;
drop function if exists app.instruction_versions_guard();

drop trigger if exists cases_creator_joins on app.cases;
drop function if exists app.cases_creator_joins();
drop policy if exists cases_create on app.cases;
create policy cases_create on app.cases for insert to app_api
  with check (
    named_expert_id = app.current_person_id()
    and exists (
      select 1 from app.memberships m
      where m.organisation_id = cases.organisation_id
        and m.person_id = app.current_person_id()
        and m.ended_at is null
        and m.role in ('owner', 'admin', 'expert')
    )
  );
drop function if exists app.is_expert_member(uuid, uuid);

create or replace function app.cases_actor() returns trigger
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

create or replace function app.case_action_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(bool_or(
           case_role = any (r.case_roles)
           or (org_role in ('owner', 'admin') and case r.org_grant
                 when 'admin' then true
                 when 'owner' then org_role = 'owner'
                 when 'team_admin' then org_kind = 'team'
                 else false end)
         ), false)
  from (values
    ('case.view_metadata',        '{named_expert,co_expert,supervisor,assistant,viewer}'::text[], 'admin'),
    ('case.view_content',         '{named_expert,co_expert,supervisor,assistant,viewer}', 'none'),
    ('case.edit_setup',           '{named_expert,assistant}', 'none'),
    ('case.upload',               '{named_expert,assistant}', 'none'),
    ('case.manage_members',       '{named_expert}', 'admin'),
    ('case.reassign_named_expert','{}', 'admin'),
    ('page.read',                 '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('page.quarantine',           '{named_expert,assistant}', 'none'),
    ('page.replace',              '{named_expert,assistant}', 'none'),
    ('page.rotate',               '{named_expert,assistant}', 'none'),
    ('fact.verify',               '{named_expert}', 'none'),
    ('question.confirm',          '{named_expert}', 'none'),
    ('opinion.write',             '{named_expert}', 'none'),
    ('comment.write',             '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('report.build',              '{named_expert,assistant}', 'none'),
    ('report.sign',               '{named_expert}', 'none'),
    ('report.serve',              '{named_expert}', 'none'),
    ('report.withdraw',           '{named_expert}', 'none'),
    ('request.draft',             '{named_expert,assistant}', 'none'),
    ('request.send',              '{named_expert}', 'none'),
    ('invoice.create',            '{named_expert,assistant}', 'team_admin'),
    ('invoice.send',              '{named_expert}', 'team_admin'),
    ('credit_note.issue',         '{named_expert}', 'team_admin'),
    ('invoice.record_payment',    '{named_expert,assistant}', 'admin'),
    ('case.export',               '{named_expert}', 'admin'),
    ('case.retention',            '{named_expert}', 'admin'),
    ('legal_hold.set',            '{named_expert}', 'owner'),
    ('legal_hold.release',        '{named_expert}', 'owner'),
    ('case.withdraw',             '{named_expert}', 'none'),
    ('support_access.grant',      '{named_expert}', 'none'),
    ('case.delete_now',           '{named_expert}', 'owner')
  ) as r (action, case_roles, org_grant)
  where r.action = case_action_allowed.action
$$;

reset role;
