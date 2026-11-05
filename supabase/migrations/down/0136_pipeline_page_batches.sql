-- 0136 down: the page-batch tables, their trigger functions and the child job kind.

set role app_migrator;

delete from app.job_kind_actions where kind = 'read_page_batch';

drop table if exists app.pipeline_batches;
drop table if exists app.pipeline_runs;
drop function if exists app.pipeline_batches_purge_lock();
drop function if exists app.pipeline_batch_child_once();

reset role;
