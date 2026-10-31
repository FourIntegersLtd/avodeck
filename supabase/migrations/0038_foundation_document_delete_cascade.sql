-- Allow deleting a document's pages even when other pages reference them
-- (duplicate_of_page_id, superseded_by_page_id, split_of_page_id).
-- Without ON DELETE SET NULL the self-referencing FKs block cascade deletes.

alter table app.pages
  drop constraint if exists pages_duplicate_of_page_id_fkey,
  add constraint pages_duplicate_of_page_id_fkey
    foreign key (duplicate_of_page_id) references app.pages (id) on delete set null;

alter table app.pages
  drop constraint if exists pages_superseded_by_page_id_fkey,
  add constraint pages_superseded_by_page_id_fkey
    foreign key (superseded_by_page_id) references app.pages (id) on delete set null;

alter table app.pages
  drop constraint if exists pages_split_of_page_id_fkey,
  add constraint pages_split_of_page_id_fkey
    foreign key (split_of_page_id) references app.pages (id) on delete set null;
