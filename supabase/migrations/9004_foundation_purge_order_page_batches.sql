-- 9004 foundation: splice `pipeline_batches` and `pipeline_runs` (pipeline migration 0136, not yet present) at the
-- front of `app.case_purge_order()` (ADR 0038). A batch references its run and both reference the case's jobs, so
-- they go before every other case table. `purge_case` skips a listed table that does not exist, so this is safe
-- before 0136 creates them; `CASE_TABLES` / `PURGE_ORDER` in `case_tables.py` match it.
--
-- Idempotent: it removes the two names wherever they are and puts them first, so the pipeline's own splice (1024)
-- can run before or after it. Written in the last foundation range, like 1023, so no later fixed-list replacement
-- of the function (0112, 0206, 0380, 0499, 0599, 0699, 0799) can wipe it.

set role app_migrator;

do $do$
declare
  mine text[] := array['pipeline_batches', 'pipeline_runs'];
  rest text[];
begin
  select coalesce(array_agg(t order by n), '{}') into rest
    from unnest(app.case_purge_order()) with ordinality as u (t, n) where t <> all (mine);
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    mine || rest
  );
end
$do$;

reset role;
