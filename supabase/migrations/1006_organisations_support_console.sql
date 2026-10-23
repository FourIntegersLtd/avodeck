-- 1006 organisations: Avodeck's support console (architecture 10.1 "Support access"; S17, F38).
--
-- Support staff have no case role, so row-level security shows them no case. These two functions give
-- current staff (`app.is_support_staff`, migration 0030) metadata only, and nothing to anyone else:
-- app.support_cases(case, email)   cases by id, or by their named expert's sign-in email (at most 50):
--                                  id, status, organisation and its kind, created_at, whether it was ever
--                                  served, and whether a grant from its named expert is active (and until
--                                  when). Never the label, names, parties or anything from the pages.
-- app.support_case_grants(case)    the case's grants: ids, who for, the reason code, dates; `active` counts
--                                  only a live grant from the case's current named expert, `mine` one that
--                                  also opens the case to the calling staff member.
-- Opening the case's content is not here: it goes through `require_case_access` under the support role.

set role app_migrator;

create function app.support_cases(p_case uuid, p_email text)
  returns table (case_id uuid, status text, organisation_id uuid, organisation_kind text, created_at timestamptz,
                 served boolean, grant_active boolean, grant_expires_at timestamptz)
  language sql stable
  security definer
  set search_path = ''
as $$
  select c.id, c.status, c.organisation_id, o.kind, c.created_at, app.case_ever_served(c.id),
         g.expires_at is not null, g.expires_at
  from app.cases c
  join app.organisations o on o.id = c.organisation_id
  left join lateral (
    select max(sg.expires_at) as expires_at from app.support_access_grants sg
    where sg.case_id = c.id and sg.granted_by = c.named_expert_id and sg.revoked_at is null
      and sg.expires_at > now()) g on true
  where app.is_support_staff(app.current_person_id())
    and (c.id = p_case
         or (p_email is not null
             and c.named_expert_id in (select p.id from app.people p where lower(p.email) = lower(p_email))))
  order by c.created_at, c.id
  limit 50
$$;

create function app.support_case_grants(p_case uuid)
  returns table (id uuid, granted_to uuid, reason text, granted_at timestamptz, expires_at timestamptz,
                 revoked_at timestamptz, active boolean, mine boolean)
  language sql stable
  security definer
  set search_path = ''
as $$
  select g.id, g.granted_to, g.reason, g.granted_at, g.expires_at, g.revoked_at, a.active,
         a.active and (g.granted_to is null or g.granted_to = app.current_person_id())
  from app.support_access_grants g
  join app.cases c on c.id = g.case_id
  cross join lateral (select g.revoked_at is null and g.expires_at > now() and g.granted_by = c.named_expert_id
                      as active) a
  where g.case_id = p_case and app.is_support_staff(app.current_person_id())
  order by g.granted_at, g.id
$$;

revoke execute on function app.support_cases(uuid, text), app.support_case_grants(uuid) from public;
grant execute on function app.support_cases(uuid, text), app.support_case_grants(uuid) to app_api;

reset role;
