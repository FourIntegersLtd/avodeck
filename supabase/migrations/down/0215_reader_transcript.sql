-- Down 0215: put the page read hash and the job kinds back, drop transcript_views.
set role app_migrator;

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
