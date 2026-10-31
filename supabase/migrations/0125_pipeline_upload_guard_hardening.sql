-- 0125 pipeline: the uploads guard really freezes parts and hashes, and a held job cannot strand an upload.
--
-- 0124 froze `received` and `chunk_sha256` only for rows already finished, so the update that moved
-- finalizing -> complete (or failed, expired, open) could still set either to anything. Now only the
-- reopen may change them, and only by dropping entries. And the daily sweep expires a finalizing upload
-- past `expires_at` whenever its job is not running (it was: done or failed only), so a job held back by a
-- case on hold cannot leave the upload finalizing for ever. Replaces two functions; no table changes.

set role app_migrator;

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
      ('open', 'complete'),  -- kept: no code path takes it now, but a branch from before 0124 may still
      ('open', 'finalizing'),
      ('open', 'cancelled'),
      ('open', 'expired'),
      ('open', 'failed'),  -- kept for the same reason (0122's `_close_failed`)
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

  -- Parts and their kept hashes (0122: the proof of what was verified) change in only two ways: while the
  -- upload stays open (parts arriving), and when a reopen (finalizing -> open) drops parts. Every other
  -- update, including the one that finishes or closes the upload, leaves both columns exactly as they were.
  if new.received is distinct from old.received or new.chunk_sha256 is distinct from old.chunk_sha256 then
    if old.status = 'open' and new.status = 'open' then
      null;
    elsif old.status = 'finalizing' and new.status = 'open' then
      if not (new.received <@ old.received) then
        perform app.refuse('a reopen can only drop parts, not add them');
      end if;
      if not (new.chunk_sha256 <@ old.chunk_sha256) then
        perform app.refuse('a reopen can only drop part hashes, not change them');
      end if;
    elsif old.status = 'open' then
      perform app.refuse('chunks and their hashes cannot change as the upload leaves open');
    elsif old.status = 'finalizing' then
      perform app.refuse('chunks and their hashes cannot change while finalizing or finishing');
    else
      perform app.refuse('chunks and hashes cannot be changed on a finished upload');
    end if;
  end if;

  return new;
end
$$;

-- Sweep (same signature as 0124)
create or replace function app.expire_uploads(p_run_date date)
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

  -- 2. Finalizing uploads past expires_at unless their job is running right now. A job that is queued or
  --    paused (a case on hold keeps its jobs queued) or never finished would otherwise leave the upload
  --    finalizing for ever, its parts never swept.
  for u in
    update app.uploads up set status = 'expired'
    where up.status = 'finalizing'
      and up.expires_at < least(now(), (p_run_date + 1)::timestamptz)
      and exists (
        select 1 from app.jobs j
        where j.id = up.finalize_job_id and j.status <> 'running'
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


reset role;
