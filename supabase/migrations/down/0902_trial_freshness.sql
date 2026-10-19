-- Down for 0902 trial: drop the build snapshot and the offline stale flag, and the guard without it.
set role app_migrator;

create or replace function app.trial_offline_guard() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  if new.status <> old.status and (old.status, new.status) not in (
    ('built', 'downloaded'), ('built', 'revoked'), ('downloaded', 'revoked')
  ) then
    raise exception 'package status % to % is not allowed', old.status, new.status using errcode = 'check_violation';
  end if;
  return new;
end
$$;

alter table app.offline_packages
  drop column if exists stale, drop column if exists stale_reason, drop column if exists stale_at,
  drop column if exists court_bundle_id, drop column if exists built_hearing_version,
  drop column if exists built_last_page_no, drop column if exists built_served_version_id;
alter table app.trial_packs
  drop column if exists stale_at, drop column if exists built_hearing_version,
  drop column if exists built_last_page_no, drop column if exists built_served_version_id;

reset role;
