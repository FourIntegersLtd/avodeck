-- Undo 1023: remove `ai_request_slots` from app.case_purge_order().
set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'ai_request_slots';
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
