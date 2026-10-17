-- Down for 0011 foundation: report version status actions.
drop trigger if exists report_versions_actor on app.report_versions;
drop function if exists app.report_versions_actor();
