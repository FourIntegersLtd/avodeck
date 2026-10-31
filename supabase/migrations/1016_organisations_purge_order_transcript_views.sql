-- 1016 (slice B's work, numbered after the 10xx range's last purge splice, as 1014 and 1015): `transcript_views`
-- joins the purge order (app.case_purge_order(), migration 0017), just after `page_reads`, as in `PURGE_ORDER` in
-- api/app/core/contracts/case_tables.py. It reads the current function and splices the table in.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'transcript_views';
  at integer;
  spliced text[];
begin
  if array_position(current_order, 'page_reads') is null then
    raise exception 'page_reads is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  at := array_position(current_order, 'page_reads');
  spliced := current_order[1:at] || array[mine] || current_order[at + 1:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
