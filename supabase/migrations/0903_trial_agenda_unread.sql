-- 0903 trial: an agenda version keeps the item numbers printed in the agenda that were not copied
-- (AUDIT-14 T-04, failure mode F86). Code counts the numbers printed on the agenda and the items it could
-- check against the agenda's own words; the numbers left over are shown as "not read, please check them
-- yourself", so an empty or partial answer never looks like a finished agenda. Numbers only, never text.

set role app_migrator;

alter table app.agenda_versions
  add column unread_numbers integer[] not null default '{}'
    check (cardinality(unread_numbers) <= 500);

reset role;
