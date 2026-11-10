-- Down for 0510 write.
set role app_migrator;

update app.witness_comparisons set status = 'refused' where status = 'waiting_to_be_read';
alter table app.witness_comparisons drop constraint witness_comparisons_status_check;
alter table app.witness_comparisons add constraint witness_comparisons_status_check
  check (status in ('queued', 'done', 'refused'));

create or replace function app.witness_comparisons_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if not exists (
      select 1 from app.documents d
        join app.cases c on c.bundle_id = d.bundle_id
      where d.id = new.document_id and c.id = new.case_id
    ) then
      perform app.refuse('a comparison is of a document of its own case');
    end if;
    if new.status <> 'queued' then
      perform app.refuse('a comparison starts queued');
    end if;
    new.requested_by := app.current_person_id();
    new.claims := null;
    new.finished_at := null;
    return new;
  end if;
  if (to_jsonb(new) - array['status', 'claims', 'finished_at'])
     is distinct from (to_jsonb(old) - array['status', 'claims', 'finished_at']) then
    perform app.refuse('a comparison keeps its document, job and who asked');
  end if;
  if new.status is distinct from old.status and not (old.status = 'queued' and new.status in ('done', 'refused')) then
    perform app.refuse(format('comparison status cannot change from %s to %s', old.status, new.status));
  end if;
  if new.status is distinct from old.status then
    new.finished_at := now();
  end if;
  return new;
end
$$;

reset role;
