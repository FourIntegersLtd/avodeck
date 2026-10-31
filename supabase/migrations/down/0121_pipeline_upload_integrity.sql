set role app_migrator;

update app.uploads set status = 'expired' where status = 'failed';

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
     and (old.status, new.status) not in (('open', 'complete'), ('open', 'expired')) then
    perform app.refuse(format('upload status cannot change from %s to %s', old.status, new.status));
  end if;
  if old.status <> 'open' and new.received is distinct from old.received then
    perform app.refuse('chunks cannot be added to a finished upload');
  end if;
  return new;
end
$$;

alter table app.documents drop column sha256_verified_at;
alter table app.uploads drop constraint uploads_status_check;
alter table app.uploads add constraint uploads_status_check check (status in ('open', 'complete', 'expired'));
alter table app.uploads drop column chunk_sha256;

reset role;
