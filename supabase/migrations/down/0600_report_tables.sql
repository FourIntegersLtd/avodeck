-- Down for 0600 report: drops slice F's tables and their guards.
set role app_migrator;
drop table if exists app.credit_notes, app.invoices, app.billing_profiles, app.time_entries, app.spot_checks,
  app.report_servings, app.report_snapshots, app.final_check_runs, app.citations, app.report_paragraphs,
  app.report_settings;
drop function if exists app.credit_notes_guard();
drop function if exists app.invoices_guard();
drop function if exists app.invoices_actor();
drop function if exists app.billing_profiles_numbers();
drop function if exists app.report_servings_guard();
drop function if exists app.report_rows_on_draft();
reset role;
