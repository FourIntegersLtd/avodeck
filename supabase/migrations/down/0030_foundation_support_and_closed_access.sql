set role app_migrator;

-- app.case_access(uuid, text) and app.can_access_case() go back to 0018: drop these, then re-run
-- that migration's two definitions.
drop function app.organisation_policy(uuid);
drop function app.case_access(uuid, text);
drop function app.case_role_allowed(text, text, text, text);
drop function app.is_support_staff(uuid);
drop table app.support_access_grants;
drop function app.support_access_grants_guard();
drop table app.support_staff;

reset role;
