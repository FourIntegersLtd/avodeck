-- Undo 0041: drop can_access_bundle, move the pages unique constraint back to
-- (case_id, bundle_page_no), and restore pages_guard to its 0002 body (page number only).
-- pages.case_id is restored by down/0222 before this runs (222 > 41).
set role app_migrator;

create or replace function app.pages_guard() returns trigger
language plpgsql as $$
begin
  if new.bundle_page_no <> old.bundle_page_no then
    perform app.refuse('a page number never changes');
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

alter table app.pages drop constraint if exists pages_bundle_page_no_key;
alter table app.pages add constraint pages_case_id_bundle_page_no_key unique (case_id, bundle_page_no);

drop function if exists app.can_access_bundle(uuid, text);

reset role;
