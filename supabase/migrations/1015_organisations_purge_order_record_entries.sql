-- 1015 (slice A's work, numbered after the 10xx range's last purge splice): `record_entries` joins the purge
-- order (app.case_purge_order(), migration 0017), just after `record_gaps`, as in `PURGE_ORDER` in
-- api/app/core/contracts/case_tables.py.
-- Later migrations (0206 to 1014) each replace the order with a fixed list, so a splice numbered 0121 would be
-- dropped again. This one reads the current function and splices the table in, keeping every other entry.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'record_entries';
  at integer;
  spliced text[];
begin
  if array_position(current_order, 'record_gaps') is null then
    raise exception 'record_gaps is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  at := array_position(current_order, 'record_gaps');
  spliced := current_order[1:at] || array[mine] || current_order[at + 1:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
