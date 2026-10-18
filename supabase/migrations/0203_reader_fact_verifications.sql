-- 0203 reader: the verification log (`fact_verifications`, rule 2, architecture 4 and 10).
--
-- One row each time the named expert checks a suggestion: Correct, Not right (with an optional
-- reason), Change a detail (old and new value, fields and cited words) or a move to a clearer copy
-- (old and new page). The fact itself holds the current state; this log keeps every earlier one.
-- Append-only and hash-chained per case like `page_reads` (0202), checked by
-- `app.fact_verifications_chain_ok(case_id)`.

set role app_migrator;

create table app.fact_verifications (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  person_id uuid not null references app.people (id),
  seq bigint not null default 0,
  action text not null check (action in ('correct', 'not_right', 'edit', 'move')),
  old_value text,
  new_value text,
  old_fields jsonb,
  new_fields jsonb,
  old_word_ids text[],
  new_word_ids text[],
  old_page_id uuid,
  new_page_id uuid,
  reason text check (char_length(reason) <= 500),
  at timestamptz not null default now(),
  prev_hash text not null default '',
  hash text not null default '',
  unique (case_id, seq)
);
create index fact_verifications_fact on app.fact_verifications (fact_id, seq);

create function app.fact_verifications_hash(r app.fact_verifications) returns text
  language sql immutable
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    r.id::text, r.case_id::text, r.fact_id::text, r.person_id::text, r.seq::text, r.action,
    coalesce(r.old_value, ''), coalesce(r.new_value, ''), coalesce(r.old_fields::text, ''),
    coalesce(r.new_fields::text, ''), coalesce(r.old_word_ids::text, ''), coalesce(r.new_word_ids::text, ''),
    coalesce(r.old_page_id::text, ''), coalesce(r.new_page_id::text, ''), coalesce(r.reason, ''),
    ((extract(epoch from r.at) * 1000000)::bigint)::text, r.prev_hash), 'UTF8')), 'hex')
$$;

create function app.fact_verifications_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous record;
begin
  if not exists (select 1 from app.facts f where f.id = new.fact_id and f.case_id = new.case_id) then
    perform app.refuse('a check is of a fact of its own case');
  end if;
  perform pg_advisory_xact_lock(7410203, hashtext(new.case_id::text));  -- one writer per case
  select r.seq, r.hash into previous from app.fact_verifications r
    where r.case_id = new.case_id order by r.seq desc limit 1;
  new.seq := coalesce(previous.seq, 0) + 1;
  new.at := now();
  new.prev_hash := coalesce(previous.hash, repeat('0', 64));
  new.hash := app.fact_verifications_hash(new);
  return new;
end
$$;

create trigger fact_verifications_chain before insert on app.fact_verifications
  for each row execute function app.fact_verifications_chain();
create trigger fact_verifications_append_only before update or delete on app.fact_verifications
  for each row execute function app.append_only_log();

create function app.fact_verifications_chain_ok(p_case_id uuid) returns boolean
  language sql stable
  set search_path = ''
as $$
  select coalesce(bool_and(s.ok), true) from (
    select r.hash = app.fact_verifications_hash(r)
       and r.seq = row_number() over w
       and r.prev_hash = coalesce(lag(r.hash) over w, repeat('0', 64)) as ok
    from app.fact_verifications r where r.case_id = p_case_id
    window w as (order by r.seq)
  ) s
$$;

alter table app.fact_verifications enable row level security;
grant select, insert on app.fact_verifications to app_api;
grant select on app.fact_verifications to app_worker;
revoke update, delete, truncate on app.fact_verifications from app_api, app_worker;

create policy fact_verifications_view on app.fact_verifications for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy fact_verifications_add on app.fact_verifications for insert to app_api
  with check (app.can_access_case(case_id, 'fact.verify') and person_id = app.current_person_id());
create policy fact_verifications_job on app.fact_verifications for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
