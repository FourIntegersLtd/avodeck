-- 0216 reader: T-10 second reading of a page we couldn't read (ADR 0030).
--
-- * `second_readings`: one row per page. The expert asks (queued), the worker masks identifiers on the page
--   picture and asks the model to copy the handwriting (done), or refuses because masking was not safe
--   (refused, with a short reason code) or the call failed. `lines` is sealed under the case key: the model's
--   copy is page content. The row never replaces the expert's text and no fact is made from it.
-- * the job kind `second_reading` needs `page.transcribe`.

set role app_migrator;

create table app.second_readings (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  status text not null default 'queued' check (status in ('queued', 'done', 'refused', 'failed')),
  reason text check (reason is null or reason ~ '^[a-z_]{1,40}$'),  -- why masking refused: a code, never text
  lines bytea,  -- sealed JSON [{text, confidence}]
  requested_by uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  requested_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (page_id)
);
create index second_readings_case on app.second_readings (case_id, status);

create function app.second_readings_same_case() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
    perform app.refuse('a second reading is of a page of its own case');
  end if;
  return new;
end
$$;
create trigger second_readings_same_case before insert on app.second_readings
  for each row execute function app.second_readings_same_case();

alter table app.second_readings enable row level security;
revoke all on app.second_readings from app_api, app_worker;
grant select, insert, update on app.second_readings to app_api;
grant select, update on app.second_readings to app_worker;

create policy second_readings_view on app.second_readings for select to app_api
  using (app.can_access_case(case_id, 'page.transcribe'));
create policy second_readings_add on app.second_readings for insert to app_api
  with check (app.can_access_case(case_id, 'page.transcribe') and requested_by = app.current_person_id());
create policy second_readings_again on app.second_readings for update to app_api
  using (app.can_access_case(case_id, 'page.transcribe'))
  with check (app.can_access_case(case_id, 'page.transcribe') and requested_by = app.current_person_id());
create policy second_readings_job on app.second_readings for select to app_worker
  using (case_id = app.current_job_case_id());
create policy second_readings_job_done on app.second_readings for update to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

insert into app.job_kind_actions (kind, action) values ('second_reading', 'page.transcribe');

reset role;
