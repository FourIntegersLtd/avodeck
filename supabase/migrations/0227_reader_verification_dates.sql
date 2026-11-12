-- 0227 reader: the check log keeps the date a check changed (security audit 16, PV-03a).
--
-- A change of a fact's date (`event_date`) and of how it was read (`date_basis`: printed, a line above,
-- assumed century, or `expert_entered` for a date the expert typed) was not in the log, so an edit that
-- changed only the date left no trace of the old one. The log now keeps the old and new date and basis, and the
-- row hash covers them.
--
-- Rows written before this migration have none of the four values. For those the hash is exactly the one
-- written at the time (nothing is appended for an empty set of dates), so they still verify.

set role app_migrator;

alter table app.fact_verifications
  add column old_event_date date,
  add column new_event_date date,
  add column old_date_basis text check (old_date_basis ~ '^[a-z][a-z0-9_]{0,39}$'),
  add column new_date_basis text check (new_date_basis ~ '^[a-z][a-z0-9_]{0,39}$');

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
    r.correction_id::text,  -- concat_ws skips a null: rows without one hash as they did (0211)
    -- the same for a row with no dates: nothing is appended, so rows written before 0227 still verify
    case when r.old_event_date is null and r.new_event_date is null
              and r.old_date_basis is null and r.new_date_basis is null then null
         else concat_ws(chr(30), coalesce(to_char(r.old_event_date, 'YYYY-MM-DD'), ''),
                        coalesce(to_char(r.new_event_date, 'YYYY-MM-DD'), ''),
                        coalesce(r.old_date_basis, ''), coalesce(r.new_date_basis, ''))
    end,
    r.prev_hash), 'UTF8')), 'hex')
$$;

reset role;
