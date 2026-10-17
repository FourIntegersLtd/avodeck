set role app_migrator;

drop function app.close_cases_without_retention(date);
drop function app.close_log_chain_heads(uuid);

reset role;
