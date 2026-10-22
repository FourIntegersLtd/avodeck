-- 1003 organisations: policy (architecture 10.1; core/contracts/org_policy.py).
--
-- app.organisation_template_exists(org, template)  for a current owner or admin of the organisation: is
--                                   the template one of the organisation's own (report_templates with
--                                   owner_type organisation)? Security definer: slice C's row-level
--                                   security shows a person only their own templates. Reads ids only.
-- organisations_policy_guard        a policy written by the running app has only the keys
--                                   `required_template_id` (an id of one of the organisation's own
--                                   templates) and `max_retention_years` (a whole number 1-100), each
--                                   possibly null; anything else is refused (22023).

set role app_migrator;

create function app.organisation_template_exists(p_org uuid, p_template uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select app.is_org_admin(p_org)
     and exists (select 1 from app.report_templates t
                 where t.id = p_template and t.owner_type = 'organisation' and t.owner_id = p_org)
$$;

create function app.organisations_policy_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  years jsonb := new.policy -> 'max_retention_years';
  tmpl jsonb := new.policy -> 'required_template_id';
begin
  if current_user <> 'app_api' or new.policy is not distinct from old.policy then
    return new;
  end if;
  if jsonb_typeof(new.policy) <> 'object'
     or exists (select 1 from jsonb_object_keys(new.policy) k
                where k not in ('required_template_id', 'max_retention_years')) then
    raise exception using errcode = '22023', message = 'an organisation policy has only its two keys';
  end if;
  if years is not null and jsonb_typeof(years) <> 'null'
     and (jsonb_typeof(years) <> 'number' or (years #>> '{}')::numeric <> trunc((years #>> '{}')::numeric)
          or (years #>> '{}')::numeric not between 1 and 100) then
    raise exception using errcode = '22023', message = 'max_retention_years is a whole number of years';
  end if;
  if tmpl is not null and jsonb_typeof(tmpl) <> 'null'
     and (jsonb_typeof(tmpl) <> 'string'
          or (tmpl #>> '{}') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          or not app.organisation_template_exists(new.id, (tmpl #>> '{}')::uuid)) then
    raise exception using errcode = '22023', message = 'required_template_id is one of the organisation''s templates';
  end if;
  return new;
end
$$;

create trigger organisations_policy_guard before update on app.organisations
  for each row execute function app.organisations_policy_guard();

revoke execute on function app.organisation_template_exists(uuid, uuid), app.organisations_policy_guard() from public;
grant execute on function app.organisation_template_exists(uuid, uuid), app.organisations_policy_guard() to app_api;

reset role;
