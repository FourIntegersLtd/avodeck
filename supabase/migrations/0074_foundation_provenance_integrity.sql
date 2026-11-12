-- 0074 foundation: source-derived columns are protected at the database; the audit log is append-only and its time
-- is the database's; AI calls name the job that made them (audit 16, PV-06, PV-13, PV-18).
--
-- PV-06  `documents_change` and `pages_change` allow any column, and `pages_guard` protected only a few. A document's
--        identity (`sha256`, `bundle_id`, `kind`, `filename`) never changes, its `storage_path` may only be cleared
--        (the purge flows) and its page count is set once. A page never moves to another document, and the
--        restricted API role (`session_user = app_api`) may no longer write what a page was read from: `image_path`
--        and `text_source` may only be cleared (read again, purge) and `ocr_confidence` and `handwriting` not at all.
--        The worker still writes them. `postgres` (break-glass, ADR 0013) is exempt from the document guard only.
-- PV-13  `at` was `coalesce(new.at, now())`, so a writer could back-date a row; the restricted roles now always get
--        the database's clock. A trigger refuses update and delete from every role but `postgres` (the revoked grants
--        never stopped the table owner, which every `security definer` function runs as). Nothing deletes audit rows:
--        a purged case's rows stay. `app.chain_checks` holds the run-time chain check's cursor (9009).
-- PV-18  `ai_calls.job_id` links a call to the job that made it (and so to `jobs.code_version`); `api_version` is the
--        OCR service's API version (the model id was already `model_id`).

set role app_migrator;

-- PV-06: documents ---------------------------------------------------------------------------------------------------
create function app.documents_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user in ('postgres', 'supabase_admin') then
    return new;
  end if;
  if new.sha256 is distinct from old.sha256 then
    perform app.refuse('a document''s checksum never changes');
  end if;
  if new.bundle_id is distinct from old.bundle_id then
    perform app.refuse('a document cannot move between bundles');
  end if;
  if new.kind is distinct from old.kind or new.filename is distinct from old.filename then
    perform app.refuse('what a document is never changes');
  end if;
  if new.storage_path is not null and new.storage_path is distinct from old.storage_path then
    perform app.refuse('a document''s file can only be cleared, never replaced');
  end if;
  if old.pages is not null and new.pages is distinct from old.pages then
    perform app.refuse('a document''s page count is set once');
  end if;
  return new;
end
$$;

create trigger documents_guard before update on app.documents
  for each row execute function app.documents_guard();

-- PV-06: pages (0050's checks, plus the two below) ---------------------------------------------------------------------
create or replace function app.pages_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.bundle_page_no is distinct from old.bundle_page_no then
    perform app.refuse('a page number never changes');
  end if;
  if new.bundle_id is distinct from old.bundle_id then
    perform app.refuse('a page cannot move between bundles');
  end if;
  if new.document_id is distinct from old.document_id then
    perform app.refuse('a page cannot move between documents');
  end if;
  if old.superseded_by_page_id is not null and new.superseded_by_page_id is distinct from old.superseded_by_page_id then
    perform app.refuse('a superseded page stays superseded');
  end if;
  if old.unreadable and not new.unreadable and new.transcribed_by_expert_text is null then
    perform app.refuse('an unreadable page becomes readable only with the expert''s transcription');
  end if;
  if session_user = 'app_api' then
    if (new.image_path is not null and new.image_path is distinct from old.image_path)
       or (new.text_source is not null and new.text_source is distinct from old.text_source)
       or new.ocr_confidence is distinct from old.ocr_confidence
       or new.handwriting is distinct from old.handwriting then
      perform app.refuse('what a page was read from is written by the worker only');
    end if;
  end if;
  return new;
end
$$;

-- PV-13: audit log ----------------------------------------------------------------------------------------------------
create or replace function app.audit_log_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous text;
begin
  perform pg_advisory_xact_lock(7410001);  -- one writer at a time; released at commit
  new.seq := nextval('app.audit_log_seq');
  if session_user in ('app_api', 'app_worker') then
    new.at := clock_timestamp();  -- a request or job never chooses the time of its own audit row
  else
    new.at := coalesce(new.at, clock_timestamp());
  end if;
  select l.hash into previous from app.audit_log l order by l.seq desc limit 1;
  new.prev_hash := coalesce(previous, repeat('0', 64));
  new.hash := app.audit_hash(new.seq, new.person_id, new.organisation_id, new.case_id, new.action, new.target,
                             new.at, new.prev_hash);
  return new;
end
$$;

create function app.audit_log_append_only() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user in ('postgres', 'supabase_admin') then  -- break-glass only; the chain check names what it changed
    return coalesce(new, old);
  end if;
  perform app.refuse('audit_log is append-only');
  return null;
end
$$;

create trigger audit_log_append_only before update or delete on app.audit_log
  for each row execute function app.audit_log_append_only();

-- PV-13c: where the run-time chain check (9009) has got to ------------------------------------------------------------
create table app.chain_checks (
  name text primary key,
  last_seq bigint not null default 0,      -- audit_log: the last row checked
  last_hash text,                          -- audit_log: its stored hash, for the next row's link
  last_case uuid,                          -- case chains: the last case checked
  breaks integer not null default 0,
  last_break_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table app.chain_checks enable row level security;
revoke all on app.chain_checks from app_api, app_worker;

-- PV-18 ---------------------------------------------------------------------------------------------------------------
-- No foreign key: the call row outlives the job's queue rows, and ids are all that is kept.
alter table app.ai_calls add column job_id uuid;
alter table app.ai_calls add column api_version text;
create index ai_calls_job on app.ai_calls (job_id) where job_id is not null;

reset role;
