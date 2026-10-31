-- Down 1015: put `uploads` back just before `pages` (the order before 0124).
set role app_migrator;

do $do$
declare
  kept text[];
  at integer;
begin
  select coalesce(array_agg(t order by n), '{}') into kept
    from unnest(app.case_purge_order()) with ordinality as u (t, n) where t <> 'uploads';
  at := array_position(kept, 'pages');
  kept := kept[1:at - 1] || array['uploads'] || kept[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    kept
  );
end
$do$;

reset role;
