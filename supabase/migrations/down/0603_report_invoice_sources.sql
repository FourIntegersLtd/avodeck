set role app_migrator;
drop function if exists app.case_invoice_rows(uuid);
drop function if exists app.case_billing_profile(uuid);
reset role;
