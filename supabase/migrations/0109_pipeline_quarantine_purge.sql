-- 0109 pipeline: deleting a quarantined page's image and text (architecture 10.3, F25, screen 30).
--
-- A logged request to the instructing party may list the quarantined pages it tells them about
-- (`quarantined_page_ids`, part of the row's hash when not empty, so earlier rows still verify).
-- The worker's daily schedule asks `quarantines_to_purge` which confirmed quarantines exist, works
-- out from the case's pack which are due, deletes their files in Storage, then calls
-- `purge_quarantined_page`: the page's words, what was read from it and its image path go, the page
-- keeps its number and stays quarantined, and the quarantine becomes `purged` (audited). Only the
-- worker may call either; the API's role cannot.

set role app_migrator;

alter table app.instructing_party_requests add column quarantined_page_ids uuid[] not null default '{}';

create or replace function app.instructing_party_requests_chain() returns trigger
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
    ((extract(epoch from new.created_at) * 1000000)::bigint)::text,
    nullif(new.quarantined_page_ids::text, '{}'), new.prev_hash), 'UTF8')), 'hex');
  return new;
end
$$;

create function app.quarantines_to_purge(p_run_date date)
  returns table (
    quarantine_id uuid, case_id uuid, page_id uuid, jurisdiction text, case_type text, specialty text,
    confirmed_on date, told boolean
  )
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select q.id, q.case_id, q.page_id, c.jurisdiction, c.case_type, c.specialty,
         (coalesce(q.confirmed_at, q.updated_at) at time zone 'UTC')::date,
         exists (
           select 1 from app.instructing_party_requests r
           where r.case_id = q.case_id and q.page_id = any (r.quarantined_page_ids)
             and r.sent_at >= coalesce(q.confirmed_at, q.updated_at) and r.sent_at < ((p_run_date + 1)::timestamp at time zone 'UTC')
         )
  from app.page_quarantines q join app.cases c on c.id = q.case_id
  where q.status = 'confirmed'
  order by q.updated_at
$$;

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

revoke execute on function app.quarantines_to_purge(date), app.purge_quarantined_page(uuid)
  from public, app_api, app_worker;
grant execute on function app.quarantines_to_purge(date), app.purge_quarantined_page(uuid) to app_worker;

reset role;
