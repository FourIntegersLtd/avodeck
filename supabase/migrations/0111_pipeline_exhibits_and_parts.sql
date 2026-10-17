-- 0111 pipeline: files that are not PDFs (F36, architecture 10.3 "Inputs other than PDF").
--
-- `document_scans.media_type` is what each document turned out to be once sniffed. An email's
-- attachments and a zip's files become documents of their own; `document_parts` says which file
-- each came from (the manifest). Audio, video, imaging and anything that cannot be paged are
-- `exhibits`: kept and listed, never given page numbers. Only the worker records parts and
-- exhibits; rows are never changed or removed (they go with the case).

set role app_migrator;

alter table app.document_scans add column media_type text;

create table app.document_parts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  parent_document_id uuid not null references app.documents (id) on delete cascade,
  document_id uuid not null unique references app.documents (id) on delete cascade,
  part_no integer not null check (part_no > 0),
  media_type text not null,
  created_at timestamptz not null default now(),
  unique (parent_document_id, part_no)
);

create index document_parts_case on app.document_parts (case_id);

create table app.exhibits (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null unique references app.documents (id) on delete cascade,
  media_type text not null,
  size bigint not null check (size >= 0),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  storage_path text not null,
  note text,
  created_at timestamptz not null default now()
);

create index exhibits_case on app.exhibits (case_id);

alter table app.document_parts enable row level security;
alter table app.exhibits enable row level security;
grant select on app.document_parts, app.exhibits to app_api;
grant select, insert on app.document_parts, app.exhibits to app_worker;
revoke insert, update, delete, truncate on app.document_parts, app.exhibits from app_api;
revoke update, delete, truncate on app.document_parts, app.exhibits from app_worker;

create policy document_parts_view on app.document_parts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_parts_job on app.document_parts to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy exhibits_view on app.exhibits for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy exhibits_job on app.exhibits to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
