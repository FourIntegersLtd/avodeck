-- 0003 foundation: case access and row-level security (rule 9, ADR 0013, docs/spec/permissions.md).
--
-- `app.case_action_allowed()` is the permissions matrix; it mirrors CASE_ACTIONS in
-- api/app/core/auth/permissions.py and tests/guards/test_permissions.py checks both against the
-- document, cell by cell. `app.can_access_case(case_id, action)` answers for the person set
-- with SET LOCAL app.person_id; with nobody set it answers false. Policies below call it, so
-- the database repeats the check `require_case_access(action)` makes in the API.
--
-- The helpers are SECURITY DEFINER (owned by app_migrator, which owns the tables) so a policy
-- can read cases and memberships without recursing into their own policies. They only ever
-- answer for app.current_person_id().

set role app_migrator;

-- The matrix ------------------------------------------------------------------------------------
-- org_grant: none; admin = owner and admin; owner = owner only; team_admin = owner and admin on
-- team organisations only.
create function app.case_action_allowed(action text, case_role text, org_role text, org_kind text)
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

-- Who the current person is on one case -----------------------------------------------------------
-- No row: the case does not exist, is deleted, or nobody is signed in.
create function app.case_access(p_case_id uuid)
  returns table (case_role text, org_role text, org_kind text)
  language sql stable
  security definer
  set search_path = ''
as $$
  select
    case when c.named_expert_id = me.id then 'named_expert'
         else (select cm.case_role from app.case_members cm
               where cm.case_id = c.id and cm.person_id = me.id and cm.removed_at is null) end,
    (select m.role from app.memberships m
     where m.organisation_id = c.organisation_id and m.person_id = me.id and m.ended_at is null),
    o.kind
  from (select app.current_person_id() as id) me
  join app.cases c on c.id = p_case_id and c.status <> 'deleted'
  join app.organisations o on o.id = c.organisation_id
  where me.id is not null
$$;

create function app.can_access_case(p_case_id uuid, p_action text) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select coalesce(
    (select app.case_action_allowed(p_action, a.case_role, a.org_role, a.org_kind) from app.case_access(p_case_id) a),
    false)
$$;

create function app.my_org_ids() returns uuid[]
  language sql stable
  security definer
  set search_path = ''
as $$
  select coalesce(array_agg(m.organisation_id), '{}')
  from app.memberships m
  where m.person_id = app.current_person_id() and m.ended_at is null
$$;

-- First sign-in: the people row and a personal organisation the person owns (architecture 10.1).
-- Creates nothing on later calls. Only ever for the signed-in person.
create function app.ensure_person(p_email text, p_full_name text, p_region text) returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  org uuid;
  created integer;
begin
  if me is null then
    raise exception using errcode = '42501', message = 'nobody is signed in';
  end if;
  insert into app.people (id, email, full_name, home_region)
  values (me, lower(p_email), p_full_name, p_region)
  on conflict (id) do nothing;
  get diagnostics created = row_count;
  if created = 0 then
    return false;
  end if;
  insert into app.organisations (name, kind, region)
  values (coalesce(p_full_name, lower(p_email)), 'personal', p_region)
  returning id into org;
  insert into app.memberships (organisation_id, person_id, role) values (org, me, 'owner');
  insert into app.audit_log (person_id, organisation_id, action, target)
  values (me, org, 'person.created', 'person:' || me::text);
  return true;
end
$$;

-- Policies ----------------------------------------------------------------------------------------
-- app_api acts for app.current_person_id(); app_worker for app.current_job_case_id().

create policy people_self on app.people to app_api
  using (id = app.current_person_id()) with check (id = app.current_person_id());

create policy experts_self on app.experts to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

create policy organisations_member on app.organisations for select to app_api
  using (id = any (app.my_org_ids()));
create policy organisations_job on app.organisations for select to app_worker
  using (id in (select c.organisation_id from app.cases c where c.id = app.current_job_case_id()));

create policy memberships_visible on app.memberships for select to app_api
  using (person_id = app.current_person_id() or organisation_id = any (app.my_org_ids()));

create policy notification_preferences_self on app.notification_preferences to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

create policy cases_view on app.cases for select to app_api
  using (app.can_access_case(id, 'case.view_metadata'));
create policy cases_change on app.cases for update to app_api
  using (app.can_access_case(id, 'case.edit_setup') or app.can_access_case(id, 'case.manage_members'))
  with check (app.can_access_case(id, 'case.edit_setup') or app.can_access_case(id, 'case.manage_members'));
create policy cases_job on app.cases for select to app_worker
  using (id = app.current_job_case_id());
create policy cases_job_update on app.cases for update to app_worker
  using (id = app.current_job_case_id()) with check (id = app.current_job_case_id());

create policy case_members_view on app.case_members for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy case_members_add on app.case_members for insert to app_api
  with check (app.can_access_case(case_id, 'case.manage_members'));
create policy case_members_change on app.case_members for update to app_api
  using (app.can_access_case(case_id, 'case.manage_members'))
  with check (app.can_access_case(case_id, 'case.manage_members'));
create policy case_members_job on app.case_members for select to app_worker
  using (case_id = app.current_job_case_id());

create policy documents_view on app.documents for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy documents_add on app.documents for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy documents_change on app.documents for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));
create policy documents_job on app.documents to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy pages_view on app.pages for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy pages_change on app.pages for update to app_api
  using (app.can_access_case(case_id, 'page.rotate')) with check (app.can_access_case(case_id, 'page.rotate'));
create policy pages_job on app.pages to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy jobs_view on app.jobs for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy jobs_add on app.jobs for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy jobs_job on app.jobs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy notifications_self on app.notifications for select to app_api
  using (person_id = app.current_person_id());
create policy notifications_mark_read on app.notifications for update to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());
create policy notifications_job on app.notifications to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy feature_flags_visible on app.feature_flags for select to app_api
  using (app.current_person_id() is not null and (
    scope in ('global', 'cell')
    or (scope = 'person' and scope_id = app.current_person_id())
    or (scope = 'organisation' and scope_id = any (app.my_org_ids()))));
create policy feature_flags_job on app.feature_flags for select to app_worker
  using (app.current_job_case_id() is not null and scope in ('global', 'cell'));

create policy audit_log_own on app.audit_log for select to app_api
  using (person_id = app.current_person_id());
create policy audit_log_write on app.audit_log for insert to app_api
  with check (person_id = app.current_person_id());
create policy audit_log_job_write on app.audit_log for insert to app_worker
  with check (person_id is null and case_id = app.current_job_case_id());

create policy idempotency_keys_self on app.idempotency_keys to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

reset role;
