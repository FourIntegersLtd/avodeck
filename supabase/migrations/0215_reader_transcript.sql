-- 0215 reader: reading by transcript (ADR 0028).
--
-- * `page_reads.via` says how the page was read: `page` (marked read in the page reader) or `transcript`
--   (the server saw every entry of the page in the transcript for long enough). The hash covers `via`
--   only when it is not `page`, so the hash of every read written before this migration is unchanged.
-- * `transcript_views`: what the reader reported seeing in the transcript, one row each time (an entry
--   on screen for some milliseconds, or the page picture opened). Append-only; the server decides from
--   these rows when a page is read. It holds no page text.
-- * the job kind `export_transcript` builds the Word file and needs `case.export`.
-- * the daily sweep lists finished `export_transcript` jobs too: see 0805 (it must run after 0802 creates it).

set role app_migrator;

alter table app.page_reads
  add column via text not null default 'page' check (via in ('page', 'transcript'));

create or replace function app.page_reads_hash(r app.page_reads) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.page_id::text, r.person_id::text, r.seq::text,
    ((extract(epoch from r.at) * 1000000)::bigint)::text, r.dwell_seconds::text,
    r.counts_for_citing::text, r.prev_hash,
    case when r.via = 'page' then null else r.via end), 'UTF8')), 'hex')
$$;

create table app.transcript_views (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  person_id uuid not null references app.people (id),
  entry_id uuid,  -- the record entry seen; null for a picture opened (no foreign key: entries are re-read)
  dwell_ms integer not null default 0 check (dwell_ms between 0 and 600000),
  picture_opened boolean not null default false,
  at timestamptz not null default now(),
  check ((entry_id is not null) <> picture_opened)
);
create index transcript_views_page on app.transcript_views (case_id, page_id, person_id);

create function app.transcript_views_same_case() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
    perform app.refuse('a view is of a page of its own case');
  end if;
  return new;
end
$$;
create trigger transcript_views_same_case before insert on app.transcript_views
  for each row execute function app.transcript_views_same_case();

create trigger transcript_views_append_only before update or delete on app.transcript_views
  for each row execute function app.append_only_log();

alter table app.transcript_views enable row level security;
revoke all on app.transcript_views from app_api, app_worker;
grant select, insert on app.transcript_views to app_api;
grant select on app.transcript_views to app_worker;

create policy transcript_views_view on app.transcript_views for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy transcript_views_add on app.transcript_views for insert to app_api
  with check (app.can_access_case(case_id, 'page.read') and person_id = app.current_person_id());
create policy transcript_views_job on app.transcript_views for select to app_worker
  using (case_id = app.current_job_case_id());

insert into app.job_kind_actions (kind, action) values ('export_transcript', 'case.export');

reset role;
