-- 0117 pipeline: job kind to backfill event_date from printed words (F12).

set role app_migrator;

insert into app.job_kind_actions (kind, action) values
  ('repair_fact_dates', 'case.upload');

reset role;
