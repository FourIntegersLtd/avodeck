-- 0023 foundation: what slice D (Understand) needs from core tables (slice brief D, permissions.md).
--
-- * Case actions `link.suggest` (case readers), `link.decide`, `finding.decide`,
--   `availability.override` and `consultation.note` (named expert only). The matrix function is
--   replaced whole (it is one table).
-- * `job_kind_actions`: `suggest_links` needs `link.suggest`; `rerun_checks` now needs
--   `finding.decide` (running the checks again is the named expert's decision).
-- * `app.set_fact_availability(fact, available_at, basis)`: writes when an entry reached the
--   clinician. The worker inside the fact's case's own job, or a person with
--   `availability.override`. Changes `available_at` and `availability_basis` only: no new fact
--   version, no status change. Anyone else, or an unknown fact, is refused (42501).
-- * `app.facts_guard()` lets that one write through without raising the version; every other
--   change still raises it.

set role app_migrator;

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
    ('page.review_duplicate',     '{named_expert,assistant}', 'none'),
    ('fact.verify',               '{named_expert}', 'none'),
    ('link.suggest',              '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('link.decide',               '{named_expert}', 'none'),
    ('finding.decide',            '{named_expert}', 'none'),
    ('availability.override',     '{named_expert}', 'none'),
    ('consultation.note',         '{named_expert}', 'none'),
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
    ('case.delete_now',           '{named_expert}', 'owner'),
    ('case.settle',               '{named_expert}', 'none'),
    ('case.archive',              '{named_expert}', 'none'),
    ('case.reopen',               '{named_expert}', 'none'),
    ('case.view_audit',           '{named_expert}', 'admin'),
    ('case.view_certificate',     '{named_expert}', 'admin'),
    ('case.hold',                 '{named_expert,assistant}', 'none'),
    ('case.change_jurisdiction',  '{named_expert}', 'none'),
    ('conflict.declare',          '{named_expert}', 'none')
  ) as r (action, case_roles, org_grant)
  where r.action = case_action_allowed.action
$$;


update app.job_kind_actions set action = 'finding.decide' where kind = 'rerun_checks';
insert into app.job_kind_actions (kind, action) values ('suggest_links', 'link.suggest');

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


create function app.set_fact_availability(p_fact_id uuid, p_available_at timestamptz, p_basis text)
  returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case uuid := (select f.case_id from app.facts f where f.id = p_fact_id);
begin
  if v_case is null
     or not (coalesce(v_case = app.current_job_case_id(), false)
             or app.can_access_case(v_case, 'availability.override')) then
    raise exception using errcode = '42501', message = 'no access to this fact';
  end if;
  perform set_config('app.fact_availability_write', p_fact_id::text, true);
  update app.facts set available_at = p_available_at, availability_basis = p_basis where id = p_fact_id;
  perform set_config('app.fact_availability_write', '', true);
end
$$;

revoke execute on function app.set_fact_availability(uuid, timestamptz, text) from public;
grant execute on function app.set_fact_availability(uuid, timestamptz, text) to app_api, app_worker;

reset role;
