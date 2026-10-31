-- Down 0215: put the page read hash, the stale-export list and the job kinds back, drop transcript_views.
set role app_migrator;

create or replace function app.close_stale_exports(p_run_date date, p_days integer, p_window integer)
  returns table (case_id uuid, job_id uuid)
  language sql stable
  security definer
  set search_path = ''
as $$
  select j.case_id, j.id from app.jobs j
  where j.kind = 'export_case' and j.finished_at is not null
    and j.finished_at::date <= p_run_date - p_days
    and j.finished_at::date > p_run_date - p_days - p_window
  order by j.finished_at, j.id
$$;

delete from app.job_kind_actions where kind = 'export_transcript';
drop table app.transcript_views;
drop function app.transcript_views_same_case();

create or replace function app.page_reads_hash(r app.page_reads) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.page_id::text, r.person_id::text, r.seq::text,
    ((extract(epoch from r.at) * 1000000)::bigint)::text, r.dwell_seconds::text,
    r.counts_for_citing::text, r.prev_hash), 'UTF8')), 'hex')
$$;
alter table app.page_reads drop column via;

reset role;
