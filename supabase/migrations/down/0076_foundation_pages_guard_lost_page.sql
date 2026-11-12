-- Back to 0074: an unreadable page becomes readable only with the expert's transcription.
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
  if new.document_id is distinct from old.document_id then
    perform app.refuse('a page cannot move between documents');
  end if;
  if old.superseded_by_page_id is not null and new.superseded_by_page_id is distinct from old.superseded_by_page_id then
    perform app.refuse('a superseded page stays superseded');
  end if;
  if old.unreadable and not new.unreadable and new.transcribed_by_expert_text is null then
    perform app.refuse('an unreadable page becomes readable only with the expert''s transcription');
  end if;
  if session_user = 'app_api' then
    if (new.image_path is not null and new.image_path is distinct from old.image_path)
       or (new.text_source is not null and new.text_source is distinct from old.text_source)
       or new.ocr_confidence is distinct from old.ocr_confidence
       or new.handwriting is distinct from old.handwriting then
      perform app.refuse('what a page was read from is written by the worker only');
    end if;
  end if;
  return new;
end
$$;

reset role;
