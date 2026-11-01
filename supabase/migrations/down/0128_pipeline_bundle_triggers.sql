-- Undo 0128: restore the pre-bundle trigger functions (case_id checks) and trigger names.
set role app_migrator;

drop trigger if exists page_analysis_same_bundle on app.page_analysis;
drop trigger if exists ocr_words_same_bundle on app.ocr_words;
drop trigger if exists page_embeddings_same_bundle on app.page_embeddings;
drop trigger if exists record_entries_same_bundle on app.record_entries;

create or replace function app.pipeline_same_case_page() returns trigger
language plpgsql as $$
begin
  if not exists (
    select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id
  ) then
    raise exception 'page belongs to another case' using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger page_analysis_same_case before insert or update on app.page_analysis
  for each row execute function app.pipeline_same_case_page();
create trigger ocr_words_same_case before insert or update on app.ocr_words
  for each row execute function app.pipeline_same_case_page();
create trigger page_embeddings_same_case before insert or update on app.page_embeddings
  for each row execute function app.pipeline_same_case_page();
create trigger record_entries_same_case before insert or update on app.record_entries
  for each row execute function app.pipeline_same_case_page();

create or replace function app.page_quarantines_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id then
    perform app.refuse('a quarantine stays on its page');
  end if;
  if new.status <> old.status and not (
    (old.status = 'suggested' and new.status in ('confirmed', 'dismissed'))
    or (old.status = 'confirmed' and new.status in ('undone', 'purged'))
  ) then
    perform app.refuse('this quarantine change is not allowed');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create or replace function app.page_replacements_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id or new.document_id <> old.document_id
     or new.requested_by <> old.requested_by then
    perform app.refuse('a replacement stays on its page and copy');
  end if;
  if old.new_page_id is not null and new.new_page_id is distinct from old.new_page_id then
    perform app.refuse('a replaced page stays replaced');
  end if;
  new.updated_at := now();
  return new;
end
$$;

reset role;
