-- 0050 foundation: restore the page-flag guard's AV409 checks dropped by 0041.
--
-- 0041 replaced app.pages_guard() to also refuse bundle_id changes, but rewrote the whole
-- function body with a generic `check_violation` (23514) instead of app.refuse() (AV409), and
-- dropped the superseded-page and unreadable-transcription checks that 0002 had added. This
-- restores all three original checks (page number, superseded page, unreadable transcription)
-- alongside 0041's two new ones (page number again, bundle_id), all raising AV409 via app.refuse().

set role app_migrator;

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

reset role;
