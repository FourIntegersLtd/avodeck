-- 0109 down: quarantined pages are no longer deleted, and requests stop listing them.

set role app_migrator;

drop function app.purge_quarantined_page(uuid);
drop function app.quarantines_to_purge(date);

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
    ((extract(epoch from new.created_at) * 1000000)::bigint)::text, new.prev_hash), 'UTF8')), 'hex');
  return new;
end
$$;

alter table app.instructing_party_requests drop column quarantined_page_ids;

reset role;
