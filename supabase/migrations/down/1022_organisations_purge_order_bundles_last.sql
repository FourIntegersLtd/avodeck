-- Undo 1022: remove `bundles` from `app.case_purge_order()` again, restoring the (buggy, but
-- prior) state left by 0112/0499/0599/0699/0799/0999's fixed-list replacements.
set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'bundles';
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into spliced
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
