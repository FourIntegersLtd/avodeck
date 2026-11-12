-- 0146 pipeline: what the pipeline keeps and flags about its own files (audit 16: PV-04, 05, 07, 08, 09, 10, 11).
--
-- PV-05  `facts.withdrawn_reason`: a suggestion the system withdraws (a re-read, a replaced or split page) is marked
--        with a short code; an expert's "not right" leaves it null. No new status. `page_analysis.refusal_codes`
--        keeps every refusal code a page's answer had (codes only, never text).
-- PV-07  `pages.content_sha256`: the SHA-256 of the page file when it was written, checked when it is read again.
--        `documents.unreadable_reason` also allows `altered`: stored bytes that no longer match what was uploaded.
-- PV-08  `pages.source_page_index`: the page's 0-based place in its source file, kept at ingest and never changed
--        (backfilled from the old derivation). `bundles.page_high_water`: the highest page number ever given out,
--        so a deleted file's numbers are never given to a later page.
-- PV-09  The progress function counts a page as set aside only when its copy is expert-confirmed.
-- PV-10  `page_analysis.read_failure`: why a page has no words (`ocr_failed`, or `altered` for a changed page file).
-- PV-11  `documents.pages_declared`: the page count the PDF itself states, to compare with the pages ingested.

set role app_migrator;

alter table app.facts add column withdrawn_reason text check (withdrawn_reason in ('reread', 'replaced', 'split'));

create function app.facts_withdrawn_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' and new.withdrawn_reason is distinct from old.withdrawn_reason then
    if session_user = 'app_api' and new.withdrawn_reason is not null then
      perform app.refuse('only the pipeline withdraws a suggestion');
    end if;
  end if;
  if new.status <> 'rejected' then
    new.withdrawn_reason := null;  -- a suggestion the expert puts back is the expert's again
  end if;
  return new;
end
$$;
create trigger facts_withdrawn_guard before insert or update on app.facts
  for each row execute function app.facts_withdrawn_guard();

alter table app.page_analysis
  add column refusal_codes text[],
  add column read_failure text check (read_failure in ('ocr_failed', 'altered'));

alter table app.pages
  add column content_sha256 text,
  add column source_page_index integer check (source_page_index >= 0);

update app.pages p
   set source_page_index = p.bundle_page_no - f.first_no
  from (select document_id, min(bundle_page_no) as first_no from app.pages
         where document_id is not null group by document_id) f
 where f.document_id = p.document_id and p.source_page_index is null;

create function app.pages_source_index_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.source_page_index is not null and new.source_page_index is distinct from old.source_page_index then
    perform app.refuse('a page''s place in its file never changes');
  end if;
  return new;
end
$$;
create trigger pages_source_index_guard before update on app.pages
  for each row execute function app.pages_source_index_guard();

alter table app.bundles add column page_high_water integer not null default 0 check (page_high_water >= 0);
update app.bundles b
   set page_high_water = coalesce((select max(p.bundle_page_no) from app.pages p where p.bundle_id = b.id), 0);

create function app.bundles_high_water_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.page_high_water < old.page_high_water then
    perform app.refuse('page numbers already given out are never given again');
  end if;
  return new;
end
$$;
create trigger bundles_high_water_guard before update on app.bundles
  for each row execute function app.bundles_high_water_guard();

alter table app.documents
  drop constraint documents_unreadable_reason_check,
  add constraint documents_unreadable_reason_check check (unreadable_reason in ('locked', 'damaged', 'altered')),
  add column pages_declared integer check (pages_declared >= 0);

create or replace function app.pipeline_report_progress(p_run uuid)
  returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  r app.pipeline_runs;
  v_phase text;
  v_total integer;
  v_done integer;
  v_pct integer;
  v_rank integer;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found then
    return;
  end if;
  if app.current_job_case_id() is distinct from r.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  if r.state in ('ocr_waiting', 'ocr_ready') then
    v_phase := 'ocr';
  elsif r.state in ('sort_waiting', 'sort_ready') then
    v_phase := 'sort';
  else
    return;  -- the tail reports its own steps
  end if;
  select count(*) filter (
           where case when v_phase = 'ocr' then p.text_source is not null
                      else coalesce(a.extracted_at is not null, false) end),
         count(*)
    into v_done, v_total
    from app.pages p
    left join app.page_analysis a on a.page_id = p.id
   where p.bundle_id = (select c.bundle_id from app.cases c where c.id = r.case_id)
     and not p.quarantined
     and (case when v_phase = 'ocr' then p.image_path is not null
               else p.text_source is not null and p.superseded_by_page_id is null
                    and (p.duplicate_of_page_id is null or coalesce(a.duplicate_status, '') <> 'confirmed') end)
     and exists (
       select 1 from app.pipeline_batches b
        where b.run_job_id = p_run and b.phase = v_phase
          and p.bundle_page_no between b.first_page_no and b.last_page_no
     );
  v_pct := case when v_phase = 'ocr' then 30 + (10 * v_done) / greatest(v_total, 1)
                else 40 + (15 * v_done) / greatest(v_total, 1) end;
  v_rank := case v_phase when 'ocr' then 1 else 2 end;
  update app.jobs j
     set pct = greatest(coalesce(j.pct, 0), v_pct),
         step = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                when 'missing' then 4 else 0 end
                     then v_phase else j.step end,
         step_done = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                     when 'missing' then 4 else 0 end
                          then case when v_total > 0 then v_done end else j.step_done end,
         step_total = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                      when 'missing' then 4 else 0 end
                           then case when v_total > 0 then v_total end else j.step_total end
   where j.id = p_run;
end
$$;
revoke execute on function app.pipeline_report_progress(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_report_progress(uuid) to app_worker;

reset role;
