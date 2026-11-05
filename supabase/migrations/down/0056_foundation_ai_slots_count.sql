-- Down 0056: the kit counts its own case's slot rows again (revert core/ai/kit.py first).
set role app_migrator;

drop function if exists app.ai_slots_in_use(text, interval);
drop index if exists app.ai_request_slots_in_flight;
alter table app.ai_request_slots drop column if exists in_flight;

reset role;
