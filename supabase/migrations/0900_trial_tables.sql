-- 0900 trial: slice I's tables (architecture 4.9): expert meetings and their participants, agenda versions and
-- items (screen 22), court bundles and the concordance (screen 23), trial pack settings and packs (screen 24)
-- and offline packages (screen 25).
--
-- Case content (agenda text, positions, reasons, notes) is sealed under the case key by the API (bytea).
-- Status changes follow docs/spec/state-machines.md and are enforced here: a signed statement and its agenda
-- items never change again; a superseded bundle is final; a built trial pack only goes stale; an offline
-- package moves built -> downloaded -> revoked. The API role never deletes. The e-sign webhook has no person,
-- so two security definer functions record its outcome by the signing reference's hash.

set role app_migrator;

create table app.expert_meetings (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  type text not null check (type ~ '^[a-z][a-z0-9_]{0,39}$'),
  hearing_id uuid,
  discussion_on date not null,
  sign_by date not null,
  sign_rule_ref text not null check (length(sign_rule_ref) <= 80),
  send_by date not null,
  send_rule_ref text not null check (length(send_rule_ref) <= 80),
  declarations jsonb not null default '[]' check (jsonb_typeof(declarations) = 'array'),
  status text not null default 'draft'
    check (status in ('draft', 'out_for_signature', 'signed', 'signed_external')),
  prepared_path text,
  prepared_sha256 text check (prepared_sha256 ~ '^[0-9a-f]{64}$'),
  prepared_pages integer check (prepared_pages >= 0),
  prepared_at timestamptz,
  signing_reference_sha256 text unique check (signing_reference_sha256 ~ '^[0-9a-f]{64}$'),
  sent_at timestamptz,
  signed_at timestamptz,
  signed_sha256 text check (signed_sha256 ~ '^[0-9a-f]{64}$'),
  certificate bytea,
  certificate_sha256 text check (certificate_sha256 ~ '^[0-9a-f]{64}$'),
  pack_version text,
  created_by uuid default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index expert_meetings_case on app.expert_meetings (case_id, created_at);

create table app.expert_meeting_participants (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  meeting_id uuid not null references app.expert_meetings (id) on delete cascade,
  name text not null check (length(name) between 1 and 200),
  discipline text check (length(discipline) <= 80),
  side text check (side ~ '^[a-z][a-z0-9_]{0,39}$'),
  person_id uuid references app.people (id),
  created_at timestamptz not null default clock_timestamp() -- keeps the order they were added in one request
);
create index expert_meeting_participants_meeting on app.expert_meeting_participants (meeting_id, created_at);

create table app.agenda_versions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  meeting_id uuid not null references app.expert_meetings (id) on delete cascade,
  version_no integer not null check (version_no >= 1),
  storage_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  job_id uuid,
  read_at timestamptz,
  item_count integer check (item_count >= 0),
  created_by uuid default app.current_person_id(),
  created_at timestamptz not null default now(),
  unique (meeting_id, version_no)
);

create table app.agenda_items (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  meeting_id uuid not null references app.expert_meetings (id) on delete cascade,
  agenda_version_id uuid not null references app.agenda_versions (id) on delete cascade,
  number integer not null check (number >= 1),
  text bytea not null,
  word_ids text[] not null default '{}',
  served_paragraph_ids uuid[] not null default '{}',
  own_position bytea,
  other_positions bytea,
  agreed boolean,
  reason bytea,
  change_note bytea,
  deviation text check (deviation in ('consistent', 'changed', 'unclear')),
  deviation_reason text check (deviation_reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  changed_from_previous text check (changed_from_previous in ('same', 'changed', 'new')),
  change_recorded_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (agenda_version_id, number)
);
create index agenda_items_meeting on app.agenda_items (meeting_id, agenda_version_id, number);

create table app.court_bundles (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  hearing_id uuid,
  version integer not null default 1 check (version >= 1),
  status text not null default 'received' check (status in ('received', 'matching', 'matched', 'superseded')),
  storage_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  page_count integer check (page_count >= 0),
  received_on date,
  job_id uuid,
  superseded_by uuid references app.court_bundles (id),
  created_by uuid default app.current_person_id(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index court_bundles_current on app.court_bundles
  (case_id, coalesce(hearing_id, '00000000-0000-0000-0000-000000000000'::uuid)) where status <> 'superseded';

create table app.concordance (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  court_bundle_id uuid not null references app.court_bundles (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  page_no integer not null,
  trial_page_no integer check (trial_page_no >= 1),
  score real not null default 0 check (score between 0 and 1),
  method text not null check (method in ('text', 'sequence', 'expert', 'none')),
  confirmed boolean not null default false,
  confirmed_by uuid,
  confirmed_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (court_bundle_id, page_id)
);
create index concordance_bundle on app.concordance (court_bundle_id, page_no);

create table app.trial_pack_settings (
  case_id uuid primary key references app.cases (id) on delete cascade,
  sections text[] not null default '{report,chronology,joint_statement,written_answers,legal_tests}',
  include_guidance boolean not null default false,
  hearing_id uuid,
  requested_job_id uuid,
  updated_by uuid default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table app.trial_packs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  hearing_id uuid,
  job_id uuid,
  report_version_id uuid references app.report_versions (id),
  court_bundle_id uuid references app.court_bundles (id),
  sections text[] not null,
  include_guidance boolean not null default false,
  sha256 text not null,
  storage_path text not null,
  page_count integer not null check (page_count >= 0),
  pack_versions jsonb not null default '{}',
  stale boolean not null default false,
  stale_reason text check (stale_reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  created_at timestamptz not null default now()
);
create index trial_packs_case on app.trial_packs (case_id, created_at desc);

create table app.offline_packages (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  hearing_id uuid,
  report_version_id uuid references app.report_versions (id),
  device_label text not null check (length(device_label) between 1 and 80),
  include_images boolean not null default false check (not include_images),
  format_version integer not null check (format_version >= 1),
  storage_path text,
  sha256 text check (sha256 ~ '^[0-9a-f]{64}$'),
  key_sealed bytea,
  status text not null default 'built' check (status in ('built', 'downloaded', 'revoked')),
  expires_at timestamptz not null,
  downloaded_at timestamptz,
  revoked_at timestamptz,
  created_by uuid default app.current_person_id(),
  created_at timestamptz not null default now()
);
create index offline_packages_case on app.offline_packages (case_id, created_at desc);

-- status changes ----------------------------------------------------------------------------------------

create function app.trial_meeting_guard() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  if old.status in ('signed', 'signed_external') then
    raise exception 'a signed statement does not change' using errcode = 'check_violation';
  end if;
  if new.status <> old.status and (old.status, new.status) not in (
    ('draft', 'out_for_signature'), ('draft', 'signed_external'), ('out_for_signature', 'draft'),
    ('out_for_signature', 'signed'), ('out_for_signature', 'signed_external')
  ) then
    raise exception 'statement status % to % is not allowed', old.status, new.status using errcode = 'check_violation';
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger expert_meetings_guard before update on app.expert_meetings
  for each row execute function app.trial_meeting_guard();

create function app.trial_agenda_guard() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  if exists (
    select 1 from app.expert_meetings m where m.id = new.meeting_id and m.status in ('signed', 'signed_external')
  ) then
    raise exception 'the statement is signed' using errcode = 'check_violation';
  end if;
  if tg_op = 'UPDATE' then
    new.version := old.version + 1;
    new.updated_at := now();
  end if;
  return new;
end
$$;
create trigger agenda_items_guard before insert or update on app.agenda_items
  for each row execute function app.trial_agenda_guard();
create trigger agenda_versions_guard before insert on app.agenda_versions
  for each row execute function app.trial_agenda_guard();

create function app.trial_bundle_guard() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  if new.status <> old.status and (old.status = 'superseded' or (old.status, new.status) not in (
    ('received', 'matching'), ('matching', 'matched'), ('received', 'matched'),
    ('received', 'superseded'), ('matching', 'superseded'), ('matched', 'superseded')
  )) then
    raise exception 'bundle status % to % is not allowed', old.status, new.status using errcode = 'check_violation';
  end if;
  new.updated_at := now();
  return new;
end
$$;
create trigger court_bundles_guard before update on app.court_bundles
  for each row execute function app.trial_bundle_guard();

create function app.trial_concordance_touch() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger concordance_touch before update on app.concordance
  for each row execute function app.trial_concordance_touch();

create function app.trial_settings_touch() returns trigger
  language plpgsql set search_path = ''
as $$
begin
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
create trigger trial_pack_settings_touch before update on app.trial_pack_settings
  for each row execute function app.trial_settings_touch();

create function app.trial_offline_guard() returns trigger
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
create trigger offline_packages_guard before update on app.offline_packages
  for each row execute function app.trial_offline_guard();

-- row-level security --------------------------------------------------------------------------------------

alter table app.expert_meetings enable row level security;
alter table app.expert_meeting_participants enable row level security;
alter table app.agenda_versions enable row level security;
alter table app.agenda_items enable row level security;
alter table app.court_bundles enable row level security;
alter table app.concordance enable row level security;
alter table app.trial_pack_settings enable row level security;
alter table app.trial_packs enable row level security;
alter table app.offline_packages enable row level security;

create policy expert_meetings_view on app.expert_meetings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy expert_meetings_add on app.expert_meetings for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy expert_meetings_change on app.expert_meetings for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));
create policy expert_meetings_job on app.expert_meetings to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy expert_meeting_participants_view on app.expert_meeting_participants for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy expert_meeting_participants_add on app.expert_meeting_participants for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
create policy expert_meeting_participants_job on app.expert_meeting_participants for select to app_worker
  using (case_id = app.current_job_case_id());

create policy agenda_versions_view on app.agenda_versions for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy agenda_versions_add on app.agenda_versions for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy agenda_versions_job on app.agenda_versions to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy agenda_items_view on app.agenda_items for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy agenda_items_change on app.agenda_items for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));
create policy agenda_items_job on app.agenda_items to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy court_bundles_view on app.court_bundles for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy court_bundles_add on app.court_bundles for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy court_bundles_change on app.court_bundles for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));
create policy court_bundles_job on app.court_bundles to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy concordance_view on app.concordance for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy concordance_change on app.concordance for update to app_api
  using (app.can_access_case(case_id, 'fact.verify')) with check (app.can_access_case(case_id, 'fact.verify'));
create policy concordance_job on app.concordance to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy trial_pack_settings_view on app.trial_pack_settings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy trial_pack_settings_add on app.trial_pack_settings for insert to app_api
  with check (app.can_access_case(case_id, 'report.build'));
create policy trial_pack_settings_change on app.trial_pack_settings for update to app_api
  using (app.can_access_case(case_id, 'report.build')) with check (app.can_access_case(case_id, 'report.build'));
create policy trial_pack_settings_job on app.trial_pack_settings for select to app_worker
  using (case_id = app.current_job_case_id());

create policy trial_packs_view on app.trial_packs for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy trial_packs_stale on app.trial_packs for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));
create policy trial_packs_job on app.trial_packs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy offline_packages_view on app.offline_packages for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy offline_packages_add on app.offline_packages for insert to app_api
  with check (app.can_access_case(case_id, 'report.sign'));
create policy offline_packages_change on app.offline_packages for update to app_api
  using (app.can_access_case(case_id, 'report.sign')) with check (app.can_access_case(case_id, 'report.sign'));

revoke delete on app.expert_meetings, app.expert_meeting_participants, app.agenda_versions, app.agenda_items,
  app.court_bundles, app.concordance, app.trial_pack_settings, app.trial_packs, app.offline_packages
  from app_api, app_worker;
revoke update on app.expert_meeting_participants, app.agenda_versions from app_api;
revoke insert on app.agenda_items, app.concordance, app.trial_packs from app_api;
revoke update on app.trial_packs from app_api;
grant update (stale, stale_reason) on app.trial_packs to app_api;
revoke update on app.trial_packs from app_worker;
revoke insert, update on app.expert_meeting_participants, app.trial_pack_settings from app_worker;
revoke all on app.offline_packages from app_worker;

-- the e-sign webhook (no person; the signing reference is the gate) --------------------------------------

create function app.trial_esign_signed(p_reference_sha256 text, p_signed_sha256 text, p_certificate bytea)
  returns text
  language plpgsql security definer set search_path = ''
as $$
declare
  m app.expert_meetings;
begin
  select * into m from app.expert_meetings where signing_reference_sha256 = p_reference_sha256 for update;
  if not found then
    return 'unknown';
  end if;
  if m.status <> 'out_for_signature' then
    return 'not_out';
  end if;
  update app.expert_meetings set status = 'signed', signed_at = now(), signed_sha256 = p_signed_sha256,
    certificate = p_certificate, certificate_sha256 = encode(sha256(p_certificate), 'hex')
    where id = m.id;
  insert into app.audit_log (person_id, case_id, action, target)
    values (null, m.case_id, 'trial.statement_signed', m.id::text);
  return 'signed';
end
$$;

create function app.trial_esign_declined(p_reference_sha256 text) returns text
  language plpgsql security definer set search_path = ''
as $$
declare
  m app.expert_meetings;
begin
  select * into m from app.expert_meetings where signing_reference_sha256 = p_reference_sha256 for update;
  if not found then
    return 'unknown';
  end if;
  if m.status <> 'out_for_signature' then
    return 'not_out';
  end if;
  update app.expert_meetings set status = 'draft', signing_reference_sha256 = null where id = m.id;
  insert into app.audit_log (person_id, case_id, action, target)
    values (null, m.case_id, 'trial.statement_declined', m.id::text);
  return 'declined';
end
$$;

revoke all on function app.trial_esign_signed(text, text, bytea) from public;
revoke all on function app.trial_esign_declined(text) from public;
grant execute on function app.trial_esign_signed(text, text, bytea) to app_api;
grant execute on function app.trial_esign_declined(text) to app_api;

reset role;
