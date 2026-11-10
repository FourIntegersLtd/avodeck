-- 0704 after serving: how many page-citing sentences code can see in the other side's expert report.
-- The worker counts them from the report's own words (not from the model), so a check that found fewer claims
-- than the report holds is shown to the expert as needing attention instead of as a finished, clean check.

set role app_migrator;

alter table app.opposing_reports
  add column claims_expected integer check (claims_expected >= 0);

reset role;
