-- 0703 after serving: who wrote the other side's expert report (screen 21).
-- The expert types the other expert's name and role when registering the report; both are optional. They
-- are the other expert's professional details, not the patient's, so they are plain text.

set role app_migrator;

alter table app.opposing_reports
  add column expert_name text check (length(expert_name) <= 200),
  add column expert_role text check (length(expert_role) <= 200);

reset role;
