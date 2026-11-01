-- 0048 foundation: add `bundles` to `app.case_purge_order()` (ADR 0034).
--
-- `purge_case_rows` (0046) already special-cases `bundles` (skips deleting it: a bundle outlives
-- the case that created it) but the loop only visits tables named in `case_purge_order()`. The
-- bundles<->cases relationship is circular (cases.bundle_id -> bundles.id; bundles.source_case_id
-- is lineage-only, no FK, never enforced at delete time), so `bundles` cannot sit before `cases`
-- in dependency order the normal way. It is appended last, after `cases`, matching `PURGE_ORDER`
-- in api/app/core/contracts/case_tables.py: the terminal entry is now `bundles`, not `cases`.
--
-- The list is read from the current function and `bundles` is appended, so every other slice's
-- entries are kept (as 1019, 1014-1018 do for their splices).

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'bundles';
  appended text[];
begin
  if array_position(current_order, 'cases') is null then
    raise exception 'cases is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  appended := current_order || array[mine];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    appended
  );
end
$do$;

reset role;
