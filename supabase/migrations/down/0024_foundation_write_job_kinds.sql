set role app_migrator;

delete from app.job_kind_actions where kind in ('suggest_points', 'compare_witness_statement');

reset role;
