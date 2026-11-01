-- Undo 0048: remove 'bundles' from app.case_purge_order(), following the splice-reversal pattern
-- used elsewhere (e.g. down/1019).
set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'bundles';
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    current_order
  );
end
$do$;

reset role;
