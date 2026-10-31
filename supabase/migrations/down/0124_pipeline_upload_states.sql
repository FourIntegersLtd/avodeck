set role app_migrator;

-- Remove mark_upload_cleaned
drop function if exists app.mark_upload_cleaned(uuid);

-- Restore expire_uploads to its pre-0124 signature (returns 2 columns, not 3)
revoke execute on function app.expire_uploads(date) from app_worker;
drop function app.expire_uploads(date);
create function app.expire_uploads(p_run_date date)
  returns table (expired_case_id uuid, expired_upload_id uuid)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  u record;
begin
  for u in
    update app.uploads up set status = 'expired'
    where up.status = 'open' and up.expires_at < least(now(), (p_run_date + 1)::timestamptz)
    returning up.id, up.case_id
  loop
    insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, u.case_id, 'upload.expired', 'upload:' || u.id
    from app.cases c where c.id = u.case_id;
    expired_case_id := u.case_id;
    expired_upload_id := u.id;
    return next;
  end loop;
end
$$;

revoke execute on function app.expire_uploads(date) from public, app_api, app_worker;
grant execute on function app.expire_uploads(date) to app_worker;

-- Restore guard to 0122 version
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
  if new.status is distinct from old.status
     and (old.status, new.status) not in (('open', 'complete'), ('open', 'expired'), ('open', 'failed')) then
    perform app.refuse(format('upload status cannot change from %s to %s', old.status, new.status));
  end if;
  if old.status <> 'open' and new.received is distinct from old.received then
    perform app.refuse('chunks cannot be added to a finished upload');
  end if;
  if old.status <> 'open' and new.chunk_sha256 is distinct from old.chunk_sha256 then
    perform app.refuse('chunk hashes cannot be changed on a finished upload');
  end if;
  return new;
end
$$;

-- Move any finalizing/cancelled rows back to open so the constraint change succeeds
alter table app.uploads disable trigger uploads_guard;
update app.uploads set status = 'open' where status in ('finalizing', 'cancelled');
alter table app.uploads enable trigger uploads_guard;

-- Restore status constraint
alter table app.uploads drop constraint uploads_status_check;
alter table app.uploads add constraint uploads_status_check
  check (status in ('open', 'complete', 'expired', 'failed'));

-- Drop new columns
alter table app.documents drop column if exists page_count_hint;
alter table app.uploads
  drop column cleaned_at,
  drop column failure,
  drop column finalize_job_id,
  drop column finalizing_at;

-- The purge order is restored by down/1015_foundation_purge_order_uploads.sql.

reset role;
