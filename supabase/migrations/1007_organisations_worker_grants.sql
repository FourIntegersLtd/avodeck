-- 1007 organisations: slice O's functions are not the worker's.
--
-- Default privileges (0000) grant every new function in `app` to app_api and app_worker. No job of slice O
-- acts for a person, so the worker may run none of the functions migrations 1000-1006 added; trigger
-- functions still fire for every role (EXECUTE is checked only when a trigger is created).

set role app_migrator;

revoke execute on function
  app.is_org_admin(uuid),
  app.create_team_organisation(text, text),
  app.organisation_members(uuid),
  app.memberships_actor(),
  app.invites_guard(),
  app.accept_invite(text),
  app.organisation_template_exists(uuid, uuid),
  app.organisations_policy_guard(),
  app.close_organisation(uuid),
  app.support_cases(uuid, text),
  app.support_case_grants(uuid)
from app_worker, public;

reset role;
