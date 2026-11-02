-- Undo 0049: drop the Bedrock slot scheduler table and its fairness column on app.jobs. Its splice
-- into app.case_purge_order() is undone by down/1023, which must run before this file.
set role app_migrator;

drop table if exists app.ai_request_slots;
alter table app.jobs drop column if exists last_granted_at;

reset role;
