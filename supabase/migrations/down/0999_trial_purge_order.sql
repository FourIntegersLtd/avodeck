-- Down for 0999 trial: the purge order without slice I's tables.
set role app_migrator;

do $do$
declare
  kept text[];
begin
  select coalesce(array_agg(t order by n), '{}') into kept
    from unnest(app.case_purge_order()) with ordinality as u (t, n)
    where t <> all (array['concordance', 'trial_packs', 'offline_packages', 'trial_pack_settings', 'court_bundles',
      'agenda_items', 'agenda_versions', 'expert_meeting_participants', 'expert_meetings']);
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    kept
  );
end
$do$;

reset role;
