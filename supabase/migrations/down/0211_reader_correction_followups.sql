-- Down for 0211: the worker no longer replaces readings, the purge keeps them (0115's function), and the
-- check log loses `correction_id`. Refuses while any check carries one (the chain would break).

set role app_migrator;

do $$ begin
  if exists (select 1 from app.fact_verifications where correction_id is not null) then
    raise exception 'checks rely on the expert''s readings; the log is append-only';
  end if;
end $$;

drop policy word_corrections_words_replaced on app.word_corrections;
revoke update (superseded_at) on app.word_corrections from app_worker;

create or replace function app.purge_quarantined_page(p_quarantine_id uuid) returns text
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

create or replace function app.fact_verifications_hash(r app.fact_verifications) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.fact_id::text, coalesce(r.person_id::text, ''), r.seq::text, r.action,
    coalesce(r.old_value, ''), coalesce(r.new_value, ''), coalesce(r.old_fields::text, ''),
    coalesce(r.new_fields::text, ''), coalesce(r.old_word_ids::text, ''), coalesce(r.new_word_ids::text, ''),
    coalesce(r.old_page_id::text, ''), coalesce(r.new_page_id::text, ''), coalesce(r.reason, ''),
    ((extract(epoch from r.at) * 1000000)::bigint)::text, r.prev_hash), 'UTF8')), 'hex')
$$;

alter table app.fact_verifications drop column correction_id;

reset role;
