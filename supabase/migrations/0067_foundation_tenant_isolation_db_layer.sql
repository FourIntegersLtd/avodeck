-- 0067 foundation: three database-level gaps in tenant isolation, closed for app_api (audit AUDIT-11 L1, L3, L4).
--
-- No API route reaches these today; this is defence in depth, so a future route or a mistake cannot cross tenants.
-- Each lock refuses only direct SQL as app_api; definer functions (ensure_person, create_replacement_case,
-- confirm_email_change, the support functions) and the worker are not affected.
--
-- L1  A case's bundle and organisation are set when the case is created and never by a plain update as app_api.
--     Re-pointing bundle_id made another tenant's pages readable (app.can_access_bundle follows cases.bundle_id);
--     re-pointing organisation_id moved a case into someone else's organisation. An INSERT as app_api may not
--     name a bundle either (cases_auto_bundle, 0040, makes one): a supplied bundle_id would be the same
--     re-pointing at creation. The refusing trigger is named so it fires BEFORE cases_auto_bundle, which
--     would otherwise fill the column first and hide what the caller sent.
-- L3  audit_log_write checked only person_id, so a person could write an audit row naming any case or
--     organisation, and the forged row showed in app.close_case_audit. A row that names a case now needs the
--     writer to be that case's named expert, a member of it (current or removed), or to hold access to it now
--     (app.can_access_case: this includes the organisation's owners and admins for administrative actions and
--     the case's custodian; a deleted case keeps the certificate access). A row that names an organisation
--     needs the writer to belong to it or to have belonged (leaving is audited after the membership has
--     ended; this is the only place "ever a member" still counts, and it covers rows with no case).
--     Current support staff are the one exception: their support actions are audited on the case or
--     organisation they look at, with or without a grant (ADR 0023).
-- L4  people_self let a person change their own email (taking the address a pending invite was sent to, so
--     app.accept_invite's address check passed), reopen a closed account, and insert their own row with any
--     email before app.ensure_person ran. As app_api none of these is allowed; app.ensure_person,
--     app.confirm_email_change and the support functions are definers and still do them.

set role app_migrator;

-- L1 ------------------------------------------------------------------------------------------------
create function app.cases_tenant_columns() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user = 'app_api' then
    if tg_op = 'INSERT' then
      raise exception using errcode = '42501', message = 'a new case gets its own bundle';
    end if;
    raise exception using errcode = '42501', message = 'a case''s bundle and organisation cannot be changed';
  end if;
  return new;
end
$$;

create trigger cases_tenant_columns before update on app.cases
  for each row
  when (old.bundle_id is distinct from new.bundle_id or old.organisation_id is distinct from new.organisation_id)
  execute function app.cases_tenant_columns();

-- Before row triggers run in name order: "cases_0_..." runs before cases_auto_bundle, so the refusal sees the
-- bundle the caller sent, not the one the next trigger fills in.
create trigger cases_0_tenant_columns_insert before insert on app.cases
  for each row
  when (new.bundle_id is not null)
  execute function app.cases_tenant_columns();

-- L3 ------------------------------------------------------------------------------------------------
-- A row may name a case the person is, or was, a member of, or has access to now. Removed members still count
-- (being removed is audited after the access has ended); a person who was only ever a member of the case's
-- organisation does not. Access is the single-case check app.can_access_case, not the set of every case.
create function app.person_took_part_in_case(p_case_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (
    select 1
    from app.cases c
    where c.id = p_case_id
      and (
        c.named_expert_id = app.current_person_id()
        or exists (select 1 from app.case_members cm
                   where cm.case_id = c.id and cm.person_id = app.current_person_id())
        or app.can_access_case(c.id, 'case.view_metadata')
        or app.can_access_case(c.id, 'case.view_certificate')
      )
  )
$$;

create function app.person_belongs_or_belonged_to_org(p_org_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.memberships m
                 where m.organisation_id = p_org_id and m.person_id = app.current_person_id())
$$;

revoke execute on function app.person_took_part_in_case(uuid), app.person_belongs_or_belonged_to_org(uuid)
  from public;
grant execute on function app.person_took_part_in_case(uuid), app.person_belongs_or_belonged_to_org(uuid)
  to app_api;

drop policy audit_log_write on app.audit_log;
create policy audit_log_write on app.audit_log for insert to app_api
  with check (
    person_id = app.current_person_id()
    and (
      app.is_support_staff(app.current_person_id())
      or (
        (case_id is null or app.person_took_part_in_case(case_id))
        and (organisation_id is null or app.person_belongs_or_belonged_to_org(organisation_id))
      )
    )
  );

-- L4 ------------------------------------------------------------------------------------------------
create function app.people_identity_columns() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user = 'app_api' then
    if tg_op = 'INSERT' then
      raise exception using errcode = '42501', message = 'a person''s row is created only at first sign-in';
    end if;
    if new.email is distinct from old.email then
      raise exception using errcode = '42501', message = 'an email address changes only through the confirmation link';
    end if;
    if old.closed_at is not null and new.closed_at is null then
      raise exception using errcode = '42501', message = 'a closed account cannot reopen itself';
    end if;
  end if;
  return new;
end
$$;

create trigger people_identity_columns before update on app.people
  for each row
  when (old.email is distinct from new.email or (old.closed_at is not null and new.closed_at is null))
  execute function app.people_identity_columns();

create trigger people_identity_columns_insert before insert on app.people
  for each row execute function app.people_identity_columns();

reset role;
