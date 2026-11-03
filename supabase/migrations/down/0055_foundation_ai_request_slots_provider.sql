-- Undo 0055: slots no longer say which provider they are for.
set role app_migrator;

drop index if exists app.ai_request_slots_provider_granted;
alter table app.ai_request_slots drop constraint if exists ai_request_slots_provider_known;
alter table app.ai_request_slots drop column if exists provider;

reset role;
