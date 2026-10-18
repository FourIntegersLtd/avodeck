-- Down 0702: restore the 0700 pack-deadline function and the 0701 deadlines guard.

set role app_migrator;

create or replace function app.after_serving_pack_deadline(
  p_case uuid, p_kind text, p_rule_ref text, p_due_on date, p_calendar text, p_timezone text, p_pack_version text
) returns uuid
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_id uuid;
begin
  if not coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case
                  or app.can_access_case(p_case, 'case.view_content'), false) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if not exists (select 1 from app.report_servings s join app.report_versions v on v.id = s.report_version_id
                 where s.case_id = p_case and v.type not in ('answers', 'supplementary')) then
    return null;
  end if;
  select d.id into v_id from app.deadlines d where d.case_id = p_case and d.kind = p_kind and d.created_from = 'pack';
  if v_id is not null then
    return v_id;
  end if;
  insert into app.deadlines (case_id, kind, rule_ref, due_on, calendar, timezone, created_from, source, pack_version)
    values (p_case, p_kind, p_rule_ref, p_due_on, p_calendar, p_timezone, 'pack', 'pack', p_pack_version)
    on conflict do nothing
    returning id into v_id;
  if v_id is null then
    select d.id into v_id from app.deadlines d where d.case_id = p_case and d.kind = p_kind and d.created_from = 'pack';
    return v_id;
  end if;
  insert into app.deadline_changes (case_id, deadline_id, old_due_on, new_due_on, reason, source, changed_by)
    values (p_case, v_id, null, p_due_on, 'served', 'pack', app.current_person_id());
  return v_id;
end
$$;

create or replace function app.deadlines_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.kind <> old.kind or new.created_from <> old.created_from
     or new.created_at <> old.created_at or new.timezone <> old.timezone then
    perform app.refuse('a deadline keeps its case, kind, origin and timezone');
  end if;
  if old.cancelled_at is not null and new.cancelled_at is distinct from old.cancelled_at then
    perform app.refuse('a cancelled deadline stays cancelled');
  end if;
  if new.due_on <> old.due_on and not exists (
    select 1 from app.deadline_changes c
    where c.deadline_id = new.id and c.old_due_on = old.due_on and c.new_due_on = new.due_on and c.at = now()
  ) then
    perform app.refuse('moving a deadline needs a deadline change with its reason');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
