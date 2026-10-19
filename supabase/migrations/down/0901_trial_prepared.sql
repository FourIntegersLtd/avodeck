-- Down for 0901 trial: drop the replies, the notes and the added columns, and the purge order without them.
set role app_migrator;

do $do$
declare
  kept text[];
begin
  select coalesce(array_agg(t order by n), '{}') into kept
    from unnest(app.case_purge_order()) with ordinality as u (t, n)
    where t <> all (array['readiness_replies', 'trial_notes']);
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    kept
  );
end
$do$;

alter table app.court_bundles drop column if exists missing_told_at;
alter table app.trial_packs drop column if exists section_pages;
drop table if exists app.trial_notes, app.readiness_replies;
drop function if exists app.trial_reply_touch();

reset role;
