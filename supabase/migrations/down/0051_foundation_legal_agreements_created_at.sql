set role app_migrator;

alter table app.legal_agreements drop column created_at;

reset role;
