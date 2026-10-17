-- 0360 setup (slice C): `app.expert_has_served` is security definer and answers about any person, so only
-- the roles that need it may run it (review finding 12): the API and the worker, through the declarations
-- guard (which runs as the caller). `public`, and so `anon` and `authenticated`, may not.

set role app_migrator;

revoke all on function app.expert_has_served(uuid) from public;
grant execute on function app.expert_has_served(uuid) to app_api, app_worker;

reset role;
