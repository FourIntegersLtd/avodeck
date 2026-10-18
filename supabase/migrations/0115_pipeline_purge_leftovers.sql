-- 0115 pipeline: a purged quarantine leaves nothing of the page behind (architecture 10.3, F25, screen 30).
--
-- 0109 deleted the page's words, what was read from it and its image path. It left the values read
-- from the page (`facts.value`, `facts.fields`) and the uploaded file the page came from. Now every
-- fact on the page is rejected and emptied, and once no page of the uploaded document is still in
-- use (each one purged), the function clears the document's `storage_path` and returns the old
-- value, so the worker deletes the stored file. It returns null when other pages still use the file.

set role app_migrator;

drop function app.purge_quarantined_page(uuid);

create function app.purge_quarantined_page(p_quarantine_id uuid) returns text
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  q record;
  doc uuid;
  path text;
begin
  select pq.id, pq.case_id, pq.page_id, pq.status into q
    from app.page_quarantines pq where pq.id = p_quarantine_id for update;
  if not found or q.status <> 'confirmed' then
    perform app.refuse('only a confirmed quarantine is deleted');
  end if;
  delete from app.ocr_words where page_id = q.page_id;
  update app.page_analysis set extraction = null, minhash = null, phash = null, printed_dates = '{}'
    where page_id = q.page_id;
  update app.facts set value = null, fields = '{}', bbox = null,
      status = 'rejected'
    where page_id = q.page_id;
  update app.pages set image_path = null, transcribed_by_expert_text = null where id = q.page_id;
  update app.page_quarantines set status = 'purged', purged_at = now() where id = q.id;
  insert into app.audit_log (organisation_id, case_id, action, target)
    select c.organisation_id, q.case_id, 'page.quarantine_purged', 'page:' || q.page_id
    from app.cases c where c.id = q.case_id;
  select p.document_id into doc from app.pages p where p.id = q.page_id;
  if doc is not null and not exists (
    select 1 from app.pages p
    where p.document_id = doc
      and not exists (select 1 from app.page_quarantines x where x.page_id = p.id and x.status = 'purged')
  ) then
    select d.storage_path into path from app.documents d where d.id = doc;
    update app.documents set storage_path = null where id = doc;
  end if;
  return path;
end
$$;

revoke execute on function app.purge_quarantined_page(uuid) from public, app_api, app_worker;
grant execute on function app.purge_quarantined_page(uuid) to app_worker;

reset role;
