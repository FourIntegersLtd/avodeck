-- Down 1014: take `case_subject_identity` out of the purge order again (the state after 0799).
set role app_migrator;

do $do$
declare
  kept text[];
begin
  select coalesce(array_agg(t order by n), '{}') into kept
    from unnest(app.case_purge_order()) with ordinality as u (t, n) where t <> 'case_subject_identity';
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    kept
  );
end
$do$;

reset role;
