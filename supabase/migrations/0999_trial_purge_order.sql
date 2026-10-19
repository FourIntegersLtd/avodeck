-- 0999 trial: slice I's case tables join the purge order (app.case_purge_order(), migration 0017), just
-- before `jobs`, in the same order as `PURGE_ORDER` in api/app/core/contracts/case_tables.py: the
-- concordance, packs and packages before the bundles, settings and report versions they reference, agenda
-- items before agenda versions, participants and versions before the meetings.
--
-- The list is read from the current function and the trial tables are spliced in, so the slices that add
-- their own tables in lower migration ranges keep their entries.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  trial text[] := array['concordance', 'trial_packs', 'offline_packages', 'trial_pack_settings', 'court_bundles',
    'agenda_items', 'agenda_versions', 'expert_meeting_participants', 'expert_meetings'];
  at integer := array_position(current_order, 'jobs');
  spliced text[];
begin
  if at is null then
    raise exception 'jobs is not in the purge order';
  end if;
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> all (trial);
  at := array_position(current_order, 'jobs');
  spliced := current_order[1:at - 1] || trial || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
