-- 0202 reader: the read log (`page_reads`, rule 3, architecture 4 and 10).
--
-- One row each time a person marks a page read: who, which page, when and how long they looked.
-- Append-only and hash-chained per case: each row is numbered and hashed onto the case's previous
-- read, so a changed or removed read breaks `app.page_reads_chain_ok(case_id)`. The running app can
-- only add rows; a trigger refuses any change or removal except `app.purge_case` deleting the case.
-- `counts_for_citing` is set by the database: only the named expert's reads make a fact citable.

set role app_migrator;

create table app.page_reads (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  person_id uuid not null references app.people (id),
  seq bigint not null default 0,
  at timestamptz not null default now(),
  dwell_seconds integer not null check (dwell_seconds between 0 and 86400),
  counts_for_citing boolean not null default false,
  prev_hash text not null default '',
  hash text not null default '',
  unique (case_id, seq)
);
create index page_reads_case_page on app.page_reads (case_id, page_id, person_id);

create function app.page_reads_hash(r app.page_reads) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.page_id::text, r.person_id::text, r.seq::text,
    ((extract(epoch from r.at) * 1000000)::bigint)::text, r.dwell_seconds::text,
    r.counts_for_citing::text, r.prev_hash), 'UTF8')), 'hex')
$$;

create function app.page_reads_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous record;
begin
  if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
    perform app.refuse('a read is of a page of its own case');
  end if;
  perform pg_advisory_xact_lock(7410202, hashtext(new.case_id::text));  -- one writer per case
  select r.seq, r.hash into previous from app.page_reads r
    where r.case_id = new.case_id order by r.seq desc limit 1;
  new.seq := coalesce(previous.seq, 0) + 1;
  new.at := now();
  new.counts_for_citing := exists (
    select 1 from app.cases c where c.id = new.case_id and c.named_expert_id = new.person_id
  );
  new.prev_hash := coalesce(previous.hash, repeat('0', 64));
  new.hash := app.page_reads_hash(new);
  return new;
end
$$;

create trigger page_reads_chain before insert on app.page_reads
  for each row execute function app.page_reads_chain();

-- Append-only: only the purge of a deleted case removes rows (it sets app.purging_case_id).
create function app.append_only_log() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and current_setting('app.purging_case_id', true) is not distinct from old.case_id::text then
    return old;
  end if;
  perform app.refuse(format('%s is append-only', tg_table_name));
  return null;
end
$$;

create trigger page_reads_append_only before update or delete on app.page_reads
  for each row execute function app.append_only_log();

-- Whether every read of the case still hashes as written and follows the one before it.
create function app.page_reads_chain_ok(p_case_id uuid) returns boolean
  language sql stable
  set search_path = ''
as $$
  select coalesce(bool_and(s.ok), true) from (
    select r.hash = app.page_reads_hash(r)
       and r.seq = row_number() over w
       and r.prev_hash = coalesce(lag(r.hash) over w, repeat('0', 64)) as ok
    from app.page_reads r where r.case_id = p_case_id
    window w as (order by r.seq)
  ) s
$$;

alter table app.page_reads enable row level security;
grant select, insert on app.page_reads to app_api;
grant select on app.page_reads to app_worker;
revoke update, delete, truncate on app.page_reads from app_api, app_worker;

create policy page_reads_view on app.page_reads for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_reads_add on app.page_reads for insert to app_api
  with check (app.can_access_case(case_id, 'page.read') and person_id = app.current_person_id());
create policy page_reads_job on app.page_reads for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
