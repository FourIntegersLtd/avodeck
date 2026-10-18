-- 0023 foundation: the opinion jobs (slice E) are queued by whoever may write the opinion (`opinion.write`).
-- suggest_points sorts verified facts for and against (T-14a/T-14b); compare_witness_statement checks a
-- witness statement against verified facts (T-14c).

set role app_migrator;

insert into app.job_kind_actions (kind, action) values
  ('suggest_points', 'opinion.write'),
  ('compare_witness_statement', 'opinion.write');

reset role;
