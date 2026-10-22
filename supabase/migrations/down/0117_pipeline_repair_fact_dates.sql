-- 0117 down: repair_fact_dates job kind.

set role app_migrator;

delete from app.job_kind_actions where kind = 'repair_fact_dates';

reset role;
