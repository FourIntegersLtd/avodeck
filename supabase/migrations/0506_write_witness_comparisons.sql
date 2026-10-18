-- 0506 write (slice E): each request to compare a witness statement with the records (T-14c, screen 14).
-- The named expert asks (`opinion.write`); the worker's `compare_witness_statement` job finds the row by
-- its job and sets it done (with the number of claims kept) or refused (the model's answer failed twice).
-- Status: queued -> done / refused, by the worker only. Nothing is deleted. No patient text is kept here.

set role app_migrator;

create table app.witness_comparisons (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null references app.documents (id) on delete cascade,
  job_id uuid references app.jobs (id) on delete set null,
  requested_by uuid,
  status text not null default 'queued' check (status in ('queued', 'done', 'refused')),
  claims integer check (claims >= 0),
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index witness_comparisons_case_document on app.witness_comparisons (case_id, document_id);
create index witness_comparisons_job on app.witness_comparisons (job_id);

create function app.witness_comparisons_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if not exists (select 1 from app.documents d where d.id = new.document_id and d.case_id = new.case_id) then
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
revoke all on function app.witness_comparisons_guard() from public;
create trigger witness_comparisons_guard before insert or update on app.witness_comparisons
  for each row execute function app.witness_comparisons_guard();

revoke delete on app.witness_comparisons from app_api, app_worker;
revoke update on app.witness_comparisons from app_api;
revoke insert on app.witness_comparisons from app_worker;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.witness_comparisons enable row level security;

create policy witness_comparisons_view on app.witness_comparisons for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy witness_comparisons_ask on app.witness_comparisons for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy witness_comparisons_job on app.witness_comparisons to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
