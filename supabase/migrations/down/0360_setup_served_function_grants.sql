-- Down for 0360 setup: back to the default grant.
set role app_migrator;
grant execute on function app.expert_has_served(uuid) to public;
reset role;
