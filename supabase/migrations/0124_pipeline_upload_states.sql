-- 0124 pipeline: upload state machine for background finalisation (plan: fix/upload-hardening).
--
-- Adds `finalizing` and `cancelled` states, a reference to the finalize job, a failure code
-- and a `cleaned_at` marker set only after Storage objects have been deleted (R8).
--
-- Transitions (guard):
--   open → finalizing  (complete claims the upload and enqueues a job)
--   open → cancelled   (client cancels)
--   open → expired     (daily sweep)
--   open → failed      (kept from 0122 until _close_failed is removed)
--   finalizing → complete  (job verifies all parts, inserts the document)
--   finalizing → open      (job finds a missing part: remove from received, client resends)
--   finalizing → failed    (unreadable part, hash or size mismatch)
--   finalizing → expired   (job dead past expires_at)
--
-- expire_uploads now also returns terminal rows needing cleanup (cleaned_at is null).

set role app_migrator;

-- New columns ---------------------------------------------------------------------------------
alter table app.uploads
  add column finalizing_at timestamptz,
  add column finalize_job_id uuid references app.jobs (id),
  add column failure text check (failure in (
    'sha256_mismatch', 'size_mismatch', 'chunk_unreadable'
  )),
  add column cleaned_at timestamptz;

-- Wider status set ----------------------------------------------------------------------------
alter table app.uploads drop constraint uploads_status_check;
alter table app.uploads add constraint uploads_status_check
  check (status in ('open', 'finalizing', 'complete', 'failed', 'expired', 'cancelled'));

-- New page_count_hint on documents (for setup to read instead of parsing PDFs in a request) ---
alter table app.documents add column page_count_hint integer check (page_count_hint > 0);

-- Guard ---------------------------------------------------------------------------------------
create or replace function app.uploads_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'open' then
      perform app.refuse('a new upload starts open');
    end if;
    return new;
  end if;

  -- Status transitions
  if new.status is distinct from old.status then
    if (old.status, new.status) not in (
      ('open', 'complete'),
      ('open', 'finalizing'),
      ('open', 'cancelled'),
      ('open', 'expired'),
      ('open', 'failed'),
      ('finalizing', 'complete'),
      ('finalizing', 'open'),
      ('finalizing', 'failed'),
      ('finalizing', 'expired')
    ) then
      perform app.refuse(format('upload status cannot change from %s to %s', old.status, new.status));
    end if;

    -- open → finalizing must set the job reference and time
    if new.status = 'finalizing' then
      if new.finalize_job_id is null or new.finalizing_at is null then
        perform app.refuse('finalizing needs a job reference and time');
      end if;
    end if;

    -- finalizing → complete must set document_id (open → complete kept for the transition)
    if old.status = 'finalizing' and new.status = 'complete' and new.document_id is null then
      perform app.refuse('complete needs a document');
    end if;
  end if;

  -- document_id only with complete (checked on every update, not just status changes)
  if new.document_id is not null and new.status <> 'complete' then
    perform app.refuse('document_id only with complete');
  end if;

  -- Chunks and chunk hashes frozen on finished uploads (0122 rule, kept)
  if old.status not in ('open', 'finalizing') and new.received is distinct from old.received then
    perform app.refuse('chunks cannot be added to a finished upload');
  end if;
  if old.status not in ('open', 'finalizing') and new.chunk_sha256 is distinct from old.chunk_sha256 then
    perform app.refuse('chunk hashes cannot be changed on a finished upload');
  end if;

  -- When finalizing, only the reopen transition (finalizing → open) may shrink received/chunk_sha256;
  -- no other change to received or chunk_sha256 is allowed while finalizing (parts cannot land).
  if old.status = 'finalizing' and new.status = 'finalizing' then
    if new.received is distinct from old.received then
      perform app.refuse('chunks cannot change while finalizing');
    end if;
    if new.chunk_sha256 is distinct from old.chunk_sha256 then
      perform app.refuse('chunk hashes cannot change while finalizing');
    end if;
  end if;

  return new;
end
$$;

-- Expire + cleanup return set -----------------------------------------------------------------
-- The return type changes (adds expired_status), so we must drop and recreate.
revoke execute on function app.expire_uploads(date) from app_worker;
drop function app.expire_uploads(date);

create function app.expire_uploads(p_run_date date)
  returns table (expired_case_id uuid, expired_upload_id uuid, expired_status text)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  u record;
  handled uuid[] := '{}';
begin
  -- 1. Newly expired open uploads
  for u in
    update app.uploads up set status = 'expired'
    where up.status = 'open' and up.expires_at < least(now(), (p_run_date + 1)::timestamptz)
    returning up.id, up.case_id
  loop
    insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, u.case_id, 'upload.expired', 'upload:' || u.id
    from app.cases c where c.id = u.case_id;
    handled := array_append(handled, u.id);
    expired_case_id := u.case_id;
    expired_upload_id := u.id;
    expired_status := 'expired';
    return next;
  end loop;

  -- 2. Finalizing uploads past expires_at whose job is done or failed (never a running one)
  for u in
    update app.uploads up set status = 'expired'
    where up.status = 'finalizing'
      and up.expires_at < least(now(), (p_run_date + 1)::timestamptz)
      and exists (
        select 1 from app.jobs j
        where j.id = up.finalize_job_id and j.status in ('done', 'failed')
      )
    returning up.id, up.case_id
  loop
    insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, u.case_id, 'upload.expired', 'upload:' || u.id
    from app.cases c where c.id = u.case_id;
    handled := array_append(handled, u.id);
    expired_case_id := u.case_id;
    expired_upload_id := u.id;
    expired_status := 'expired';
    return next;
  end loop;

  -- 3. Terminal rows needing cleanup (no document, cleaned_at still null, not just handled above)
  for u in
    select up.id, up.case_id, up.status
    from app.uploads up
    where up.status in ('expired', 'cancelled', 'failed')
      and up.document_id is null
      and up.cleaned_at is null
      and up.id <> all(handled)
  loop
    expired_case_id := u.case_id;
    expired_upload_id := u.id;
    expired_status := u.status;
    return next;
  end loop;
end
$$;

revoke execute on function app.expire_uploads(date) from public, app_api, app_worker;
grant execute on function app.expire_uploads(date) to app_worker;

-- Worker-only: mark an upload's Storage objects as deleted -------------------------------------
create or replace function app.mark_upload_cleaned(p_upload_id uuid)
  returns void
  language sql
  security definer
  set search_path = ''
as $$
  update app.uploads set cleaned_at = now()
  where id = p_upload_id
    and status in ('expired', 'cancelled', 'failed')
    and document_id is null;
$$;

revoke execute on function app.mark_upload_cleaned(uuid) from public, app_api;
grant execute on function app.mark_upload_cleaned(uuid) to app_worker;

-- The purge order is owned by 1019_organisations_purge_order_uploads.sql (it must run after every fixed-list purge migration).

reset role;
