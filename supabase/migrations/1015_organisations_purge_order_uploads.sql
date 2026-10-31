-- 1015 (foundation work): `uploads` is purged immediately before `jobs` (app.case_purge_order(), migration 0017),
-- as in `PURGE_ORDER` in api/app/core/contracts/case_tables.py. uploads.finalize_job_id (0124) references jobs,
-- so uploads must be deleted first.
-- Written here, not in the pipeline range, because 0499, 0599, 0699, 0799 and 0999 each replace the order
-- with a fixed list and would wipe a splice made at 0124; 1014 documents the same trap. Named for the
-- range it sits in, as the migration-range guard requires; foundation owns the change, as with 1014.
-- The next migration that writes a fixed purge list must keep `uploads` before `jobs` or add a later splice.
--
-- The list is read from the current function and the table is spliced in, so every other slice's entries
-- are kept.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'uploads';
  at integer;
  spliced text[];
begin
  if array_position(current_order, 'jobs') is null then
    raise exception 'jobs is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  at := array_position(current_order, 'jobs');
  spliced := current_order[1:at - 1] || array[mine] || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
