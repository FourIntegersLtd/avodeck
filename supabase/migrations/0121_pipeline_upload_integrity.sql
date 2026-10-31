-- 0121 pipeline: proof that a stored file is the file that was sent (F30, F31; architecture 5.3).
--
-- Each part's SHA-256, worked out by the server from the bytes it received, is kept per part number in
-- `uploads.chunk_sha256` ({"1": "<hex>", ...}); a part sent again replaces its entry. A column rather than a
-- table: the hashes live and die with the upload row, so no purge-order change is needed.
-- An upload whose whole-file SHA-256 does not match what was stored is closed as `failed` and can never be
-- finished. A document records when its SHA-256 was last checked against the stored bytes.

set role app_migrator;

alter table app.uploads add column chunk_sha256 jsonb not null default '{}'::jsonb
  check (jsonb_typeof(chunk_sha256) = 'object');

alter table app.uploads drop constraint uploads_status_check;
alter table app.uploads add constraint uploads_status_check
  check (status in ('open', 'complete', 'expired', 'failed'));

alter table app.documents add column sha256_verified_at timestamptz;

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
  return new;
end
$$;

reset role;
