-- 0300 setup (slice C): the expert profile tables (architecture 4.1, screen 02).
-- * expert_roles: jobs the expert has held, each with a date range (open-ended while current).
-- * expert_training: courses and qualifications.
-- * report_templates: the Word file reports are built into; the file itself is in Storage, encrypted.
--   Person templates only here; organisation templates come with slice O.
-- * declarations: wording the expert adds to the pack's fixed declarations, one row per jurisdiction
--   and kind. No change once the expert has a served report (SQLSTATE AV409, 409 in the API).
-- * signatures: the signature image (in Storage, encrypted); the newest row is the one used.
-- Every row belongs to one person; only that person sees or changes it.

set role app_migrator;

create table app.expert_roles (
  id uuid primary key default gen_random_uuid(),
  expert_id uuid not null references app.people (id) on delete cascade,
  specialty text not null check (specialty ~ '^[a-z][a-z0-9-]{0,39}$'),
  role_title text not null check (length(role_title) between 1 and 200),
  setting text check (setting ~ '^[a-z][a-z0-9_]{0,39}$'),
  period daterange not null check (not lower_inf(period)),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index expert_roles_expert on app.expert_roles (expert_id);

create table app.expert_training (
  id uuid primary key default gen_random_uuid(),
  expert_id uuid not null references app.people (id) on delete cascade,
  title text not null check (length(title) between 1 and 200),
  year integer check (year between 1900 and 2200),
  created_at timestamptz not null default now()
);
create index expert_training_expert on app.expert_training (expert_id);

create table app.report_templates (
  id uuid primary key default gen_random_uuid(),
  owner_type text not null check (owner_type in ('person', 'organisation')),
  owner_id uuid not null,
  filename text not null check (length(filename) between 1 and 255),
  storage_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint not null check (size_bytes > 0),
  heading_map jsonb not null default '[]',
  style_map jsonb not null default '[]',
  mapping jsonb not null default '[]',
  version integer not null check (version > 0),
  revision integer not null default 1,
  created_by uuid references app.people (id) default app.current_person_id(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_type, owner_id, version)
);

create table app.declarations (
  id uuid primary key default gen_random_uuid(),
  expert_id uuid not null references app.people (id) on delete cascade,
  jurisdiction text not null check (jurisdiction ~ '^[a-z][a-z0-9-]{0,39}$'),
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  text text check (length(text) <= 4000),
  locked_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (expert_id, jurisdiction, kind)
);

create table app.signatures (
  id uuid primary key default gen_random_uuid(),
  expert_id uuid not null references app.people (id) on delete cascade,
  method text not null check (method in ('drawn', 'photo')),
  storage_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  width integer check (width > 0),
  height integer check (height > 0),
  created_at timestamptz not null default now()
);
create index signatures_expert on app.signatures (expert_id, created_at desc);

-- Has this person ever been the named expert on a served case? ---------------------------------------
create function app.expert_has_served(p_person_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.cases c
                 where c.named_expert_id = p_person_id and app.case_ever_served(c.id))
$$;

create function app.declarations_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if app.expert_has_served(coalesce(new.expert_id, old.expert_id)) then
    perform app.refuse('declarations are fixed after the first served report');
  end if;
  if tg_op = 'INSERT' then
    new.version := 1;
  else
    new.version := old.version + 1;
    new.updated_at := now();
  end if;
  return new;
end
$$;
create trigger declarations_guard before insert or update on app.declarations
  for each row execute function app.declarations_guard();
revoke delete on app.declarations from app_api, app_worker;

-- Row versions for ETag/If-Match on the editable rows -----------------------------------------------
create function app.setup_bump_version() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.version := 1;
  else
    new.version := old.version + 1;
    new.updated_at := now();
  end if;
  return new;
end
$$;
create trigger expert_roles_version before insert or update on app.expert_roles
  for each row execute function app.setup_bump_version();

create function app.report_templates_revision() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.revision := 1;
  else
    if (new.owner_type, new.owner_id, new.version, new.storage_path, new.sha256)
       is distinct from (old.owner_type, old.owner_id, old.version, old.storage_path, old.sha256) then
      perform app.refuse('a stored template never changes; upload a new version');
    end if;
    new.revision := old.revision + 1;
    new.updated_at := now();
  end if;
  return new;
end
$$;
create trigger report_templates_revision before insert or update on app.report_templates
  for each row execute function app.report_templates_revision();

revoke update on app.signatures from app_api, app_worker;

-- Access: each person, their own rows ------------------------------------------------------------------
alter table app.expert_roles enable row level security;
alter table app.expert_training enable row level security;
alter table app.report_templates enable row level security;
alter table app.declarations enable row level security;
alter table app.signatures enable row level security;

create policy expert_roles_self on app.expert_roles to app_api
  using (expert_id = app.current_person_id()) with check (expert_id = app.current_person_id());
create policy expert_training_self on app.expert_training to app_api
  using (expert_id = app.current_person_id()) with check (expert_id = app.current_person_id());
create policy report_templates_self on app.report_templates to app_api
  using (owner_type = 'person' and owner_id = app.current_person_id())
  with check (owner_type = 'person' and owner_id = app.current_person_id());
create policy declarations_self on app.declarations to app_api
  using (expert_id = app.current_person_id()) with check (expert_id = app.current_person_id());
create policy signatures_self on app.signatures to app_api
  using (expert_id = app.current_person_id()) with check (expert_id = app.current_person_id());

reset role;
