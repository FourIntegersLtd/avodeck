-- Undo 0049: drop the Bedrock slot scheduler table, its fairness column on app.jobs, and its splice
-- into app.case_purge_order().
set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'ai_request_slots';
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    current_order
  );
end
$do$;

drop table if exists app.ai_request_slots;
alter table app.jobs drop column if exists last_granted_at;

reset role;
