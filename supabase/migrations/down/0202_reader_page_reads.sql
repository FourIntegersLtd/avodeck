-- 0202 down: the read log goes.

set role app_migrator;

drop table app.page_reads cascade;
drop function app.page_reads_chain_ok(uuid);
drop function app.page_reads_chain();
drop function app.append_only_log();

reset role;
