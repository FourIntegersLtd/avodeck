-- 0207 reader: re-mapped facts are logged. When slice A's job reads a page again (turned, or typed
-- out by the expert) and moves a checked fact onto the same words in the new reading, it appends a
-- `fact_verifications` row with action `remapped`, no person, and the old and new cited words.
-- The hash now reads a missing person as '' (unchanged for every row that has one).

set role app_migrator;

alter table app.fact_verifications drop constraint fact_verifications_action_check;
alter table app.fact_verifications add constraint fact_verifications_action_check
  check (action in ('correct', 'not_right', 'edit', 'move', 'remapped'));
alter table app.fact_verifications alter column person_id drop not null;
alter table app.fact_verifications add constraint fact_verifications_person
  check (person_id is not null or action = 'remapped');

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

grant insert on app.fact_verifications to app_worker;

create policy fact_verifications_remapped on app.fact_verifications for insert to app_worker
  with check (case_id = app.current_job_case_id() and person_id is null and action = 'remapped');

reset role;
