-- 0017 foundation: what slice C needs from the foundation (docs/slices/0-foundation.md "Changes for slice C").
-- * Three case actions join the matrix (permissions.md): `case.hold` (named expert, assistant),
--   `case.change_jurisdiction` (named expert, step-up in the API) and `conflict.declare` (named expert).
-- * `cases_actor` checks `on_hold` against `case.hold` (it was `case.retention`) and the pack columns
--   (jurisdiction, case_type, specialty, pack_versions) against `case.change_jurisdiction`, before
--   serving only.
-- * Creating a case: an assistant may name an owner, admin or expert member of the organisation and
--   joins the case as its assistant; everyone else still names themselves.
-- * `instruction_versions`: a contract table slice C fills. Each set of instructions is a numbered
--   version; locking the latest one (`locked_at`, needs `question.confirm`) locks the questions and is
--   never undone; a newer version reopens them. `questions.instruction_version_id` must name a version
--   of the same case, and a confirmed or edited question returns to suggested only when it moves to a
--   newer version of a case that has never been served.

set role app_migrator;

-- The matrix, with the three new actions -----------------------------------------------------------
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
    ('case.delete_now',           '{named_expert}', 'owner'),
    ('case.hold',                 '{named_expert,assistant}', 'none'),
    ('case.change_jurisdiction',  '{named_expert}', 'none'),
    ('conflict.declare',          '{named_expert}', 'none')
  ) as r (action, case_roles, org_grant)
  where r.action = case_action_allowed.action
$$;

-- Each sensitive column of a case needs its own action -----------------------------------------------
create or replace function app.cases_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  own constant text[] := array['named_expert_id', 'retention_policy', 'delete_after', 'on_hold', 'status',
                               'jurisdiction', 'case_type', 'specialty', 'pack_versions', 'version', 'updated_at'];
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
  if (new.retention_policy, new.delete_after) is distinct from (old.retention_policy, old.delete_after) then
    needed := array_append(needed, 'case.retention');
  end if;
  if new.on_hold is distinct from old.on_hold then
    needed := array_append(needed, 'case.hold');
  end if;
  if (new.jurisdiction, new.case_type, new.specialty, new.pack_versions)
     is distinct from (old.jurisdiction, old.case_type, old.specialty, old.pack_versions) then
    if app.case_ever_served(old.id) then
      raise exception using errcode = '42501', message = 'the packs cannot change after serving';
    end if;
    needed := array_append(needed, 'case.change_jurisdiction');
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

-- Creating a case ------------------------------------------------------------------------------------
-- Whether a person is an active owner, admin or expert of an organisation (someone who can be named).
create function app.is_expert_member(p_organisation_id uuid, p_person_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.memberships m
                 where m.organisation_id = p_organisation_id and m.person_id = p_person_id
                   and m.ended_at is null and m.role in ('owner', 'admin', 'expert'))
$$;
revoke execute on function app.is_expert_member(uuid, uuid) from public;
grant execute on function app.is_expert_member(uuid, uuid) to app_api;

drop policy cases_create on app.cases;
create policy cases_create on app.cases for insert to app_api
  with check (
    (named_expert_id = app.current_person_id() and app.is_expert_member(organisation_id, app.current_person_id()))
    or (named_expert_id is distinct from app.current_person_id()
        and app.is_expert_member(organisation_id, named_expert_id)
        and exists (
          select 1 from app.memberships m
          where m.organisation_id = cases.organisation_id
            and m.person_id = app.current_person_id()
            and m.ended_at is null
            and m.role = 'assistant'
        ))
  );

-- An assistant who creates a case for an expert joins it as its assistant, so they can carry on.
create function app.cases_creator_joins() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me constant uuid := app.current_person_id();
begin
  if me is not null and me is distinct from new.named_expert_id then
    insert into app.case_members (case_id, person_id, case_role, added_by) values (new.id, me, 'assistant', me);
  end if;
  return new;
end
$$;
revoke execute on function app.cases_creator_joins() from public;

create trigger cases_creator_joins after insert on app.cases
  for each row execute function app.cases_creator_joins();

-- Instruction versions (contract table; slice C fills it) ---------------------------------------------
create table app.instruction_versions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  version_no integer not null check (version_no > 0),
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  document_id uuid references app.documents (id) on delete set null,
  received_on date,
  locked_at timestamptz,
  locked_by uuid references app.people (id),
  created_by uuid references app.people (id) default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (case_id, version_no),
  unique (case_id, id)
);

create function app.instruction_versions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  open_fields constant text[] := array['document_id', 'received_on', 'locked_at', 'locked_by', 'version',
                                       'updated_at'];
begin
  if tg_op = 'INSERT' then
    if new.locked_at is not null or new.locked_by is not null then
      perform app.refuse('a new instruction version starts unlocked');
    end if;
    new.version := 1;
    return new;
  end if;
  if (to_jsonb(new) - open_fields) is distinct from (to_jsonb(old) - open_fields) then
    perform app.refuse('an instruction version keeps its case, number and kind');
  end if;
  if old.locked_at is not null
     and (new.locked_at, new.locked_by, new.document_id, new.received_on)
         is distinct from (old.locked_at, old.locked_by, old.document_id, old.received_on) then
    perform app.refuse('a locked instruction version never changes; add a new version');
  end if;
  if old.locked_at is null and new.locked_at is not null then
    if current_user = 'app_api' and not app.can_access_case(old.case_id, 'question.confirm') then
      raise exception using errcode = '42501', message = 'this change needs question.confirm';
    end if;
    new.locked_by := app.current_person_id();
  elsif new.locked_by is distinct from old.locked_by then
    perform app.refuse('locked_by is set by the lock');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create trigger instruction_versions_guard before insert or update on app.instruction_versions
  for each row execute function app.instruction_versions_guard();

alter table app.instruction_versions enable row level security;
revoke delete on app.instruction_versions from app_api, app_worker;

create policy instruction_versions_view on app.instruction_versions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy instruction_versions_add on app.instruction_versions for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy instruction_versions_change on app.instruction_versions for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));
create policy instruction_versions_job on app.instruction_versions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- Questions name a version of their own case ----------------------------------------------------------
alter table app.questions add constraint questions_instruction_version_fk
  foreign key (case_id, instruction_version_id) references app.instruction_versions (case_id, id);
create index questions_instruction_version on app.questions (instruction_version_id);

create or replace function app.questions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  reopened boolean := false;
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
  if old.status in ('confirmed', 'edited') and new.status = 'suggested'
     and new.instruction_version_id is distinct from old.instruction_version_id
     and not app.case_ever_served(old.case_id) then
    reopened := coalesce((select v.version_no from app.instruction_versions v where v.id = new.instruction_version_id)
                         > coalesce((select v.version_no from app.instruction_versions v
                                     where v.id = old.instruction_version_id), 0), false);
  end if;
  if new.status is distinct from old.status and not reopened
     and (old.status, new.status) not in (('suggested', 'confirmed'), ('suggested', 'edited')) then
    perform app.refuse(format('question status cannot change from %s to %s', old.status, new.status));
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
