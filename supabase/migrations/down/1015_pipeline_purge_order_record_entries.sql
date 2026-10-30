-- Down 1015: take `record_entries` out of the purge order again.
set role app_migrator;

do $do$
declare
  kept text[];
begin
  select coalesce(array_agg(t order by n), '{}') into kept
    from unnest(app.case_purge_order()) with ordinality as u (t, n) where t <> 'record_entries';
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    kept
  );
end
$do$;

reset role;
