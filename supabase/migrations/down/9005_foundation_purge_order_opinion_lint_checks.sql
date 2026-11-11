-- Undo 9005: remove `opinion_lint_checks` from app.case_purge_order().
set role app_migrator;

do $do$
declare
  spliced text[] := array_remove(app.case_purge_order(), 'opinion_lint_checks');
begin
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
