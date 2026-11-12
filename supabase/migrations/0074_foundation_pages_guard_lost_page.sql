-- 0074 foundation: a lost page that has been cut again and read may become readable (audit 13, DP-09 review P1).
--
-- A page no parser child could cut or draw is marked `unreadable` while its image and text are still unset; a
-- later run cuts it and reads it. 0050 refuses to clear `unreadable` without the expert's transcription, so such
-- a page stayed listed unreadable even with its words. The clear is now also allowed when ALL of these hold: the
-- page had never been read (`text_source` was unset), the update records a read (`text_source` set), the page has
-- an image, and it has words. Any other clear (a page that was read and found unreadable, no image, no words, no
-- read recorded) still needs the transcription. The words are looked up as the caller, so RLS applies.

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
  if old.unreadable and not new.unreadable and new.transcribed_by_expert_text is null
     and not (
       old.text_source is null
       and new.text_source is not null
       and new.image_path is not null
       and exists (select 1 from app.ocr_words w where w.page_id = new.id)
     ) then
    perform app.refuse('an unreadable page becomes readable only with the expert''s transcription');
  end if;
  return new;
end
$$;

reset role;
