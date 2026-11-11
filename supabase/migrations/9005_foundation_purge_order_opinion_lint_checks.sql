-- 9005 audit step 14: `opinion_lint_checks` (0511) joins app.case_purge_order(), just before `lint_issues`, in the same
-- place as `PURGE_ORDER` in api/app/core/contracts/case_tables.py. It references `opinions`, so it goes before them.
-- Idempotent. Written in the last foundation range so no later fixed-list replacement of the function can wipe it.

set role app_migrator;

do $do$
declare
  current text[] := app.case_purge_order();
  pos int;
  spliced text[];
begin
  current := array_remove(current, 'opinion_lint_checks');
  pos := array_position(current, 'lint_issues');
  spliced := current[1:pos - 1] || array['opinion_lint_checks'] || current[pos:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
