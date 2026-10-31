-- Back to the 0124 guard and sweep.
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

-- Sweep as 0124 had it
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


reset role;
