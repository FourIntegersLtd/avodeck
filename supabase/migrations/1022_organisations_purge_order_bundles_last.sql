-- 1022 (foundation work): restore `bundles` as the terminal entry of `app.case_purge_order()`.
-- 0048 appended `bundles` after `cases` (ADR 0034: bundles cannot sit before cases because the
-- bundles<->cases relationship is circular and unenforced at delete time), but 0112 (slice A) then
-- replaced the function with a fixed array written before the bundle split, silently dropping
-- `bundles` again. 0499, 0599, 0699, 0799 and 0999 each do the same (replace with a fixed list),
-- so each has the same trap as 1014/1019 describe, but none of them re-add `bundles` either -
-- it is simply missing from every later fixed list, all the way through 1021.
-- Written here, in the organisations range, for the same reason 1014/1019 are: foundation owns
-- the purge-order contract, this file must be named for the range it sits in, and placing it after
-- every other slice's splice (1014-1021) means it runs last and is not itself at risk of being
-- wiped out by a later fixed-list replacement, matching `PURGE_ORDER` in
-- api/app/core/contracts/case_tables.py, whose terminal entry is `bundles`.
--
-- The list is read from the current function and `bundles` is appended at the end (removing any
-- stale earlier copy first), so every other slice's entries are kept in their existing order.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'bundles';
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into spliced
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  spliced := spliced || array[mine];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
