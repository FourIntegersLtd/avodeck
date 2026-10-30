-- 0397 setup: the sealed subject identity joins the purge order (app.case_purge_order(), migration 0017),
-- just before `case_subject_keys`, in the same order as `PURGE_ORDER` in
-- api/app/core/contracts/case_tables.py. The list is read from the current function and the table is
-- spliced in, so the entries other slices added in lower ranges stay.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  at integer;
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> 'case_subject_identity';
  at := array_position(current_order, 'case_subject_keys');
  if at is null then
    raise exception 'case_subject_keys is not in the purge order';
  end if;
  spliced := current_order[1:at - 1] || array['case_subject_identity'] || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
