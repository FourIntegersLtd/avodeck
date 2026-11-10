-- Down for 0705 after serving: `checked_by` goes back to code or model; claims the expert settled lose the label.
set role app_migrator;

alter table app.opposing_claims drop constraint if exists opposing_claims_checked_by_check;
update app.opposing_claims set checked_by = null where checked_by = 'expert';
alter table app.opposing_claims
  add constraint opposing_claims_checked_by_check check (checked_by in ('code', 'model'));

reset role;
