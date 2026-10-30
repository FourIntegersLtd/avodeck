-- 1014 foundation: slice C's `case_subject_identity` rejoins the purge order (app.case_purge_order(), migration
-- 0017), just before `case_subject_keys`, as in `PURGE_ORDER` in api/app/core/contracts/case_tables.py.
-- Migration 0397 spliced it in, but 0499, 0599, 0699 and 0799 each replaced the order with a fixed list
-- and dropped it. Written here in the 10xx range because it must run after all of them (a lower number
-- would be overwritten again); numbered by foundation.
--
-- The list is read from the current function and the table is spliced in, so every other slice's entries
-- are kept.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'case_subject_identity';
  at integer;
  spliced text[];
begin
  if array_position(current_order, 'case_subject_keys') is null then
    raise exception 'case_subject_keys is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  at := array_position(current_order, 'case_subject_keys');
  spliced := current_order[1:at - 1] || array[mine] || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
