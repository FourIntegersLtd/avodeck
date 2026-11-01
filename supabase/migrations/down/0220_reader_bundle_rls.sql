-- Undo 0220: restore page_reads_chain, transcript_views_same_case and second_readings_same_case
-- to plain pages.case_id checks (pages.case_id is restored by down/0222, which runs before this).
set role app_migrator;

create or replace function app.page_reads_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous record;
begin
  if not exists (
    select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id
  ) then
    perform app.refuse('a read is of a page of its own case');
  end if;
  perform pg_advisory_xact_lock(7410202, hashtext(new.case_id::text));  -- one writer per case
  select r.seq, r.hash into previous from app.page_reads r
    where r.case_id = new.case_id order by r.seq desc limit 1;
  new.seq := coalesce(previous.seq, 0) + 1;
  new.at := now();
  new.counts_for_citing := exists (
    select 1 from app.cases c where c.id = new.case_id and c.named_expert_id = new.person_id
  );
  new.prev_hash := coalesce(previous.hash, repeat('0', 64));
  new.hash := app.page_reads_hash(new);
  return new;
end
$$;

create or replace function app.transcript_views_same_case() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (
    select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id
  ) then
    perform app.refuse('a view is of a page of its own case');
  end if;
  return new;
end
$$;

create or replace function app.second_readings_same_case() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (
    select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id
  ) then
    perform app.refuse('a second reading is of a page of its own case');
  end if;
  return new;
end
$$;

reset role;
