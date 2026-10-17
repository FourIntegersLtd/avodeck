-- 0016 foundation: mfa_attempts and job_kind_actions record when each row was made (timestamps guard).

set role app_migrator;

alter table app.mfa_attempts add column created_at timestamptz not null default now();
alter table app.job_kind_actions add column created_at timestamptz not null default now();

reset role;
