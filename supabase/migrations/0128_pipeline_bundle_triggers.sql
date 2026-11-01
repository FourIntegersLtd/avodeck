-- 0128 pipeline: rewrite cross-table integrity triggers on pipeline-owned tables for the bundle split.
--
-- 1. pipeline_same_case_page → pipeline_same_bundle_page (check bundle_id, not case_id)
-- 2. page_quarantines_guard, page_replacements_guard: also refuse bundle_id changes
--
-- Numbered in the pipeline range because page_analysis, ocr_words, page_embeddings,
-- record_entries, page_quarantines and page_replacements are created by pipeline migrations
-- 0100-0121, which run after any foundation-numbered migration (0043 already updated
-- facts_guard, the one trigger function on foundation tables).

set role app_migrator;

-- ── 1. Replace pipeline_same_case_page with pipeline_same_bundle_page ────────

-- Drop the old triggers first (they reference the old function)
drop trigger if exists page_analysis_same_case on app.page_analysis;
drop trigger if exists ocr_words_same_case on app.ocr_words;
drop trigger if exists page_embeddings_same_case on app.page_embeddings;
drop trigger if exists record_entries_same_case on app.record_entries;

-- Replace the function body (keeps the old name for simplicity; the logic changes)
create or replace function app.pipeline_same_case_page() returns trigger
language plpgsql as $$
begin
  if not exists (
    select 1 from app.pages p where p.id = new.page_id and p.bundle_id = new.bundle_id
  ) then
    raise exception 'page belongs to another bundle' using errcode = 'check_violation';
  end if;
  return new;
end
$$;

-- Recreate triggers (they check bundle_id now, not case_id)
create trigger page_analysis_same_bundle before insert or update on app.page_analysis
  for each row execute function app.pipeline_same_case_page();
create trigger ocr_words_same_bundle before insert or update on app.ocr_words
  for each row execute function app.pipeline_same_case_page();
create trigger page_embeddings_same_bundle before insert or update on app.page_embeddings
  for each row execute function app.pipeline_same_case_page();
create trigger record_entries_same_bundle before insert or update on app.record_entries
  for each row execute function app.pipeline_same_case_page();

-- ── 2. Update page_quarantines_guard: also refuse bundle_id changes ──────────

create or replace function app.page_quarantines_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id or new.bundle_id <> old.bundle_id then
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

-- ── 3. Update page_replacements_guard: also refuse bundle_id changes ─────────

create or replace function app.page_replacements_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.page_id <> old.page_id or new.document_id <> old.document_id
     or new.requested_by <> old.requested_by or new.bundle_id <> old.bundle_id then
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
