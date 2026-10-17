set role app_migrator;

drop function app.close_release_expired_holds(date);
drop function app.close_auto_archive(date, integer);
drop function app.close_certificate_recipients(uuid);
drop function app.close_case_audit(uuid);
drop trigger legal_holds_not_during_deletion on app.legal_holds;
drop function app.legal_holds_not_during_deletion();
drop table app.deletion_certificates;
drop table app.legal_holds;

reset role;
