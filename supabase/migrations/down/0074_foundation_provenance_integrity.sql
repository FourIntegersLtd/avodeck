-- Undo 0074: the guards go, `pages_guard` is 0050's again, the audit trigger is 0004's, the new columns are dropped.
set role app_migrator;

alter table app.ai_calls drop column if exists api_version;
alter table app.ai_calls drop column if exists job_id;
drop table if exists app.chain_checks;
drop trigger if exists audit_log_append_only on app.audit_log;
drop function if exists app.audit_log_append_only();

create or replace function app.audit_log_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous text;
begin
  perform pg_advisory_xact_lock(7410001);
  new.seq := nextval('app.audit_log_seq');
  new.at := coalesce(new.at, now());
  select l.hash into previous from app.audit_log l order by l.seq desc limit 1;
  new.prev_hash := coalesce(previous, repeat('0', 64));
  new.hash := app.audit_hash(new.seq, new.person_id, new.organisation_id, new.case_id, new.action, new.target,
                             new.at, new.prev_hash);
  return new;
end
$$;

create or replace function app.pages_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.bundle_page_no is distinct from old.bundle_page_no then
    perform app.refuse('a page number never changes');
  end if;
  if new.bundle_id is distinct from old.bundle_id then
    perform app.refuse('a page cannot move between bundles');
  end if;
  if old.superseded_by_page_id is not null and new.superseded_by_page_id is distinct from old.superseded_by_page_id then
    perform app.refuse('a superseded page stays superseded');
  end if;
  if old.unreadable and not new.unreadable and new.transcribed_by_expert_text is null then
    perform app.refuse('an unreadable page becomes readable only with the expert''s transcription');
  end if;
  return new;
end
$$;

drop trigger if exists documents_guard on app.documents;
drop function if exists app.documents_guard();

reset role;
