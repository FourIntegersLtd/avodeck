-- 0126 pipeline: let a split page's halves go when the page they came from is deleted.
-- 0038 does this for the other two self-references on app.pages. split_of_page_id is added by
-- 0119, so on a fresh database its cascade can only be set here, after that column exists.
-- Without ON DELETE SET NULL the self-referencing foreign key blocks deleting a document's pages.
set role app_migrator;

alter table app.pages
  drop constraint if exists pages_split_of_page_id_fkey,
  add constraint pages_split_of_page_id_fkey
    foreign key (split_of_page_id) references app.pages (id) on delete set null;

reset role;
