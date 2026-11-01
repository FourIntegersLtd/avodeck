-- Undo 0049: drop the Bedrock slot scheduler table and its fairness column on app.jobs.
set role app_migrator;

drop table if exists app.ai_request_slots;
alter table app.jobs drop column if exists last_granted_at;

reset role;
