-- Down for 0704 after serving: drop the count of page-citing sentences code saw in the other side's report.
set role app_migrator;

alter table app.opposing_reports drop column if exists claims_expected;

reset role;
