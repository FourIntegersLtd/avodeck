set role app_migrator;

drop function app.cases_due_for_deletion(date);
drop function app.replay_deletion_ledger();
drop function app.purge_case(uuid);
drop function app.purge_case_rows(uuid);
drop function app.case_legally_held(uuid);
drop function app.case_purge_order();
drop trigger report_versions_no_delete on app.report_versions;
drop function app.report_versions_no_delete();
drop table app.deletion_ledger;
drop function app.deletion_ledger_append_only();
drop trigger cases_settled_at on app.cases;
drop function app.cases_settled_at();
-- app.cases_actor() and app.case_action_allowed() go back to 0012 and 0003: re-run those definitions.
update app.cases set label = '' where label is null;
alter table app.cases drop constraint cases_label_until_deleted;
alter table app.cases alter column label set not null;
alter table app.cases drop constraint cases_retention_policy_code;
alter table app.cases drop column settled_at;

reset role;
