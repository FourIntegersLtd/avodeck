set role app_migrator;

drop function app.case_member_names(uuid);
drop trigger jobs_requested_by on app.jobs;
drop function app.jobs_requested_by();
alter table app.jobs drop column requested_by;
drop trigger cases_closed_at on app.cases;
drop function app.cases_closed_at();
alter table app.cases drop column closed_at;
drop function app.release_case_hold(uuid);
drop function app.job_waits_for_hold(uuid, boolean);
drop function app.notify(uuid, uuid, uuid, text, boolean);
-- app.notify(uuid, uuid, uuid, text) and app.notifications_daily(date) go back to 0009: re-run those definitions.
update app.notifications set held_at = null where held_at is not null;
drop index app.notifications_held;
alter table app.notifications drop column held_at;

reset role;
