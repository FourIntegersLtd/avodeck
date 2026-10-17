-- 0106 pipeline: requests to the instructing party for missing records and clearer copies (screen 08b).
--
-- One row per request the expert or their assistant sends: the open missing items and the pages
-- needing a clearer copy it asked for, and its subject and text sealed with the case key. The log is
-- append-only: the running app cannot change or remove a row, and each row is numbered and hashed
-- onto the case's previous request, so a changed or removed request breaks the chain.

set role app_migrator;

create table app.instructing_party_requests (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  seq bigint not null default 0,
  missing_item_ids uuid[] not null default '{}',
  page_ids uuid[] not null default '{}',
  subject bytea not null,
  body_text bytea not null,
  sent_via text not null check (sent_via in ('mailto', 'gmail', 'graph')),
  sent_at timestamptz not null default now(),
  message_id text,
  follow_up_on date,
  created_by uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  prev_hash text not null default '',
  hash text not null default '',
  unique (case_id, seq)
);

create function app.instructing_party_requests_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous record;
begin
  perform pg_advisory_xact_lock(7410106, hashtext(new.case_id::text));  -- one writer per case
  select r.seq, r.hash into previous from app.instructing_party_requests r
    where r.case_id = new.case_id order by r.seq desc limit 1;
  new.seq := coalesce(previous.seq, 0) + 1;
  new.created_at := now();
  new.prev_hash := coalesce(previous.hash, repeat('0', 64));
  new.hash := encode(sha256(convert_to(concat_ws(chr(31),
    new.id::text, new.case_id::text, new.seq::text, new.missing_item_ids::text, new.page_ids::text,
    encode(new.subject, 'hex'), encode(new.body_text, 'hex'), new.sent_via,
    ((extract(epoch from new.sent_at) * 1000000)::bigint)::text, coalesce(new.message_id, ''),
    coalesce(new.follow_up_on::text, ''), new.created_by::text,
    ((extract(epoch from new.created_at) * 1000000)::bigint)::text, new.prev_hash), 'UTF8')), 'hex');
  return new;
end
$$;

create trigger instructing_party_requests_chain before insert on app.instructing_party_requests
  for each row execute function app.instructing_party_requests_chain();

create index instructing_party_requests_case on app.instructing_party_requests (case_id, seq);

alter table app.instructing_party_requests enable row level security;
grant select, insert on app.instructing_party_requests to app_api;
grant select on app.instructing_party_requests to app_worker;
revoke update, delete, truncate on app.instructing_party_requests from app_api, app_worker;

create policy instructing_party_requests_view on app.instructing_party_requests for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy instructing_party_requests_send on app.instructing_party_requests for insert to app_api
  with check (app.can_access_case(case_id, 'request.draft') and created_by = app.current_person_id());
create policy instructing_party_requests_job on app.instructing_party_requests for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
