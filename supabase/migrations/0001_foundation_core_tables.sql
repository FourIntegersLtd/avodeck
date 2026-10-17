-- 0001 foundation: core tables (architecture 4.1-4.4, 4.11).
--
-- Every table lives in `app`, is owned by app_migrator and has row-level security switched on
-- here, so it fails closed until the policies in 0003 arrive. Timestamps are timestamptz (UTC).
-- Pack seams (jurisdiction, case_type, specialty, expert_role) are plain text keys: the pack
-- loader validates them; nothing country-, case-type- or specialty-specific is written here.

set role app_migrator;

-- 4.1 People ------------------------------------------------------------------------------------
create table app.people (
  id uuid primary key,  -- = the Supabase Auth user id
  email text not null,
  full_name text,
  locale text,
  timezone text,
  home_region text,  -- the cell that created the person; set by app.ensure_person()
  records_custodian_name text,
  records_custodian_email text,
  created_at timestamptz not null default now(),
  closed_at timestamptz
);
create unique index people_email_key on app.people (lower(email));

create table app.experts (
  person_id uuid primary key references app.people (id) on delete cascade,
  profession text,
  specialties text[] not null default '{}',
  registration_body text,
  registration_number text,
  hourly_rate numeric(10, 2),
  currency text,
  default_retention text,
  default_jurisdiction text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 4.2 Organisations -----------------------------------------------------------------------------
create table app.organisations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text not null check (kind in ('personal', 'team')),
  region text not null,
  billing_profile_id uuid,
  policy jsonb not null default '{}',
  created_at timestamptz not null default now(),
  closed_at timestamptz
);

create table app.memberships (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references app.organisations (id),
  person_id uuid not null references app.people (id),
  role text not null check (role in ('owner', 'admin', 'expert', 'assistant', 'viewer')),
  started_at timestamptz not null default now(),
  ended_at timestamptz
);
create unique index memberships_one_active on app.memberships (organisation_id, person_id) where ended_at is null;
create index memberships_person on app.memberships (person_id) where ended_at is null;

create table app.notification_preferences (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references app.people (id) on delete cascade,
  organisation_id uuid not null references app.organisations (id),
  channel text not null check (channel in ('in_app', 'email')),
  digest boolean not null default false,
  muted_kinds text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (person_id, organisation_id, channel)
);

-- 4.3 Case ----------------------------------------------------------------------------------------
create table app.cases (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references app.organisations (id),
  named_expert_id uuid not null references app.people (id),
  jurisdiction text not null,
  case_type text not null,
  specialty text not null,
  expert_role text not null,
  pack_versions jsonb not null default '{}',
  label text not null,
  subject_status text,
  party_role text,
  instructing_party_type text,
  court_track text,
  due_date date,
  period_start date,
  period_end date,
  stage text,
  status text not null default 'draft'
    check (status in ('draft', 'processing', 'active', 'served', 'settled', 'withdrawn', 'archived', 'deleted')),
  on_hold boolean not null default false,
  retention_policy text,
  delete_after date,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index cases_organisation on app.cases (organisation_id);
create index cases_named_expert on app.cases (named_expert_id);

create table app.case_members (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  person_id uuid not null references app.people (id),
  case_role text not null check (case_role in ('co_expert', 'supervisor', 'assistant', 'viewer')),
  added_by uuid not null references app.people (id),
  added_at timestamptz not null default now(),
  removed_at timestamptz
);
create unique index case_members_one_active on app.case_members (case_id, person_id) where removed_at is null;
create index case_members_person on app.case_members (person_id) where removed_at is null;

-- 4.4 Bundle (columns used by every slice; slice A fills OCR and source fields) -----------------
create table app.documents (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  kind text not null,
  filename text not null,
  storage_path text,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  pages integer,
  received_on date,
  obtained_how text,
  bundle_version integer not null default 1,
  uploaded_by uuid references app.people (id),
  created_at timestamptz not null default now()
);
create index documents_case on app.documents (case_id);

create table app.pages (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null references app.documents (id) on delete cascade,
  bundle_page_no integer not null check (bundle_page_no > 0),
  source_type text,
  image_path text,
  text_source text,
  ocr_confidence real,
  handwriting boolean,
  rotation integer not null default 0 check (rotation in (0, 90, 180, 270)),
  duplicate_of_page_id uuid references app.pages (id),
  superseded_by_page_id uuid references app.pages (id),
  quarantined boolean not null default false,
  quarantine_reason text,
  quarantined_by uuid references app.people (id),
  quarantined_at timestamptz,
  unreadable boolean not null default false,
  relevance text,
  language text,
  transcribed_by_expert_text text,
  created_at timestamptz not null default now(),
  unique (case_id, bundle_page_no)
);

-- 4.11 System -------------------------------------------------------------------------------------
create table app.jobs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references app.cases (id) on delete cascade,
  kind text not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'paused')),
  priority integer not null default 100,
  attempts integer not null default 0,
  pct smallint check (pct between 0 and 100),
  eta_seconds integer,
  code_version text,
  error_code text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now()
);
create index jobs_case on app.jobs (case_id);
create index jobs_queue on app.jobs (priority, created_at) where status = 'queued';

create table app.notifications (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references app.people (id) on delete cascade,
  organisation_id uuid not null references app.organisations (id),
  case_id uuid references app.cases (id) on delete cascade,
  kind text not null,
  created_at timestamptz not null default now(),
  read_at timestamptz,
  emailed_at timestamptz
);
create index notifications_person on app.notifications (person_id, created_at desc);

create table app.feature_flags (
  id uuid primary key default gen_random_uuid(),
  key text not null,
  scope text not null check (scope in ('global', 'cell', 'organisation', 'person')),
  scope_id uuid,
  enabled boolean not null default false,
  created_at timestamptz not null default now(),
  check ((scope in ('global', 'cell')) = (scope_id is null))
);
create unique index feature_flags_key_scope on app.feature_flags (key, scope, coalesce(scope_id, '00000000-0000-0000-0000-000000000000'));

-- No foreign keys: audit rows outlive the people, organisations and cases they mention (10.9).
-- seq, at, prev_hash and hash are filled by the chain trigger in 0004.
create table app.audit_log (
  seq bigint primary key,
  person_id uuid,
  organisation_id uuid,
  case_id uuid,
  action text not null,
  target text not null,
  at timestamptz not null default now(),
  prev_hash text not null,
  hash text not null
);
create index audit_log_case on app.audit_log (case_id);
create sequence app.audit_log_seq owned by app.audit_log.seq;

-- Idempotency-Key replies (F27): the first answer to a state-changing request, kept 24 hours.
create table app.idempotency_keys (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null,
  key text not null check (length(key) between 1 and 255),
  fingerprint text not null,
  status_code integer not null,
  response_body bytea not null,
  response_headers jsonb not null default '{}',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '24 hours',
  unique (person_id, key)
);

-- Fail closed until 0003 adds the policies.
alter table app.people enable row level security;
alter table app.experts enable row level security;
alter table app.organisations enable row level security;
alter table app.memberships enable row level security;
alter table app.notification_preferences enable row level security;
alter table app.cases enable row level security;
alter table app.case_members enable row level security;
alter table app.documents enable row level security;
alter table app.pages enable row level security;
alter table app.jobs enable row level security;
alter table app.notifications enable row level security;
alter table app.feature_flags enable row level security;
alter table app.audit_log enable row level security;
alter table app.idempotency_keys enable row level security;

-- Append-only or staff-only for the running app.
revoke update, delete on app.audit_log from app_api, app_worker;
revoke insert, update, delete on app.feature_flags from app_api, app_worker;
revoke delete on app.memberships, app.cases, app.case_members from app_api, app_worker;

reset role;
