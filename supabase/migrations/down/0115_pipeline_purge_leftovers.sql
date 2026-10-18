-- Down for 0115: the purge function as 0109 left it (facts and the source file are kept).
set role app_migrator;

drop function app.purge_quarantined_page(uuid);

create function app.purge_quarantined_page(p_quarantine_id uuid) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  q record;
begin
  select pq.id, pq.case_id, pq.page_id, pq.status into q
    from app.page_quarantines pq where pq.id = p_quarantine_id for update;
  if not found or q.status <> 'confirmed' then
    perform app.refuse('only a confirmed quarantine is deleted');
  end if;
  delete from app.ocr_words where page_id = q.page_id;
  update app.page_analysis set extraction = null, minhash = null, phash = null, printed_dates = '{}'
    where page_id = q.page_id;
  update app.pages set image_path = null, transcribed_by_expert_text = null where id = q.page_id;
  update app.page_quarantines set status = 'purged', purged_at = now() where id = q.id;
  insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, q.case_id, 'page.quarantine_purged', 'page:' || q.page_id
    from app.cases c where c.id = q.case_id;
end
$$;

revoke execute on function app.purge_quarantined_page(uuid) from public, app_api, app_worker;
grant execute on function app.purge_quarantined_page(uuid) to app_worker;

reset role;
