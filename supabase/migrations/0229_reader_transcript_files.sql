-- 0229 reader: the hash of a transcript file is written down when it is built (security audit 16).
--
-- The file is built by the worker, stored sealed, and handed over once. Nothing recorded what was built, so a
-- file swapped in storage in between would have been handed over as the expert's transcript. The worker now
-- writes the SHA-256 of the file when it stores it; the download checks the file it is about to hand over
-- against it (409 `hash_mismatch`) and sends the hash in `X-Content-SHA256`.
-- One row per built file, never changed; it goes with its job.

set role app_migrator;

create table app.transcript_files (
  job_id uuid primary key references app.jobs (id) on delete cascade,
  fmt text not null check (fmt in ('docx', 'pdf')),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  at timestamptz not null default now()
);

create function app.transcript_files_append_only() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and not exists (select 1 from app.jobs j where j.id = old.job_id) then
    return old;
  end if;
  perform app.refuse('transcript_files is append-only');
  return null;
end
$$;
create trigger transcript_files_append_only before update or delete on app.transcript_files
  for each row execute function app.transcript_files_append_only();

alter table app.transcript_files enable row level security;
revoke all on app.transcript_files from app_api, app_worker;
grant select on app.transcript_files to app_api;
grant insert on app.transcript_files to app_worker;

create policy transcript_files_view on app.transcript_files for select to app_api
  using (exists (select 1 from app.jobs j where j.id = job_id and app.can_access_case(j.case_id, 'case.export')));
create policy transcript_files_built on app.transcript_files for insert to app_worker
  with check (exists (select 1 from app.jobs j where j.id = job_id and j.case_id = app.current_job_case_id()));

reset role;
