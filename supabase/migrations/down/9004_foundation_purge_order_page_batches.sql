-- Undo 9004: remove `pipeline_batches` and `pipeline_runs` from app.case_purge_order().
set role app_migrator;

do $do$
declare
  mine text[] := array['pipeline_batches', 'pipeline_runs'];
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into spliced
    from unnest(app.case_purge_order()) with ordinality as u (t, n) where t <> all (mine);
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
