set role app_migrator;

alter table app.job_kind_actions drop column created_at;
alter table app.mfa_attempts drop column created_at;

reset role;
