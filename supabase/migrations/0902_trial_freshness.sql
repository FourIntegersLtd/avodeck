-- 0902 trial: trial packs and offline copies keep what they were built from, so they can be shown as stale
-- when new material arrives after they were built (architecture 10.7; state-machines.md "Trial pack and
-- offline package freshness"; S10, F35). `app/core/contracts/trial_freshness.py` reads these columns.
--
-- built_hearing_version: the version of the hearing (slice G) the pack or copy is for, when built.
-- built_last_page_no: the last numbered page of the records, when built.
-- built_served_version_id: the newest served version (report, answers or supplementary), when built.
-- offline_packages.court_bundle_id: the court bundle whose trial page numbers the copy carries.
-- offline_packages.stale / stale_reason / stale_at: as on trial packs; the reason is a short key, never
--   text, and a stale copy never becomes current again (a new copy is made instead).

set role app_migrator;

alter table app.trial_packs
  add column stale_at timestamptz,
  add column built_hearing_version integer check (built_hearing_version >= 1),
  add column built_last_page_no integer check (built_last_page_no >= 0),
  add column built_served_version_id uuid references app.report_versions (id);
grant update (stale_at) on app.trial_packs to app_api;

alter table app.offline_packages
  add column stale boolean not null default false,
  add column stale_reason text check (stale_reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  add column stale_at timestamptz,
  add column court_bundle_id uuid references app.court_bundles (id),
  add column built_hearing_version integer check (built_hearing_version >= 1),
  add column built_last_page_no integer check (built_last_page_no >= 0),
  add column built_served_version_id uuid references app.report_versions (id);

create or replace function app.trial_offline_guard() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  if new.status <> old.status and (old.status, new.status) not in (
    ('built', 'downloaded'), ('built', 'revoked'), ('downloaded', 'revoked')
  ) then
    raise exception 'package status % to % is not allowed', old.status, new.status using errcode = 'check_violation';
  end if;
  if old.stale and not new.stale then
    raise exception 'a stale copy does not become current again' using errcode = 'check_violation';
  end if;
  return new;
end
$$;

reset role;
