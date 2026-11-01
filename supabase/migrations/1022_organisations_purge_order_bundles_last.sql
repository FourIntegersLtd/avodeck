-- 1022 (foundation work, numbered in the organisations range like 1019): `bundles` is the last table in app.case_purge_order(), as in `PURGE_ORDER`
-- (api/app/core/contracts/case_tables.py). 0048 added it, but 0499, 0599, 0699, 0799 and 0999 each replace
-- the order with a fixed list and dropped it. Same splice as 1019: read the current list, put `bundles`
-- at the end, keep every other entry.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> 'bundles';
  spliced := current_order || array['bundles'];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
