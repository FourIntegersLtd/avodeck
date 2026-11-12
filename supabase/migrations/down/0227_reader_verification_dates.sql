-- Down 0227: the check log stops keeping dates; the hash goes back to the 0211 form.
set role app_migrator;

create or replace function app.fact_verifications_hash(r app.fact_verifications) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.fact_id::text, coalesce(r.person_id::text, ''), r.seq::text, r.action,
    coalesce(r.old_value, ''), coalesce(r.new_value, ''), coalesce(r.old_fields::text, ''),
    coalesce(r.new_fields::text, ''), coalesce(r.old_word_ids::text, ''), coalesce(r.new_word_ids::text, ''),
    coalesce(r.old_page_id::text, ''), coalesce(r.new_page_id::text, ''), coalesce(r.reason, ''),
    ((extract(epoch from r.at) * 1000000)::bigint)::text,
    r.correction_id::text,
    r.prev_hash), 'UTF8')), 'hex')
$$;

alter table app.fact_verifications
  drop column if exists old_event_date, drop column if exists new_event_date,
  drop column if exists old_date_basis, drop column if exists new_date_basis;

reset role;
