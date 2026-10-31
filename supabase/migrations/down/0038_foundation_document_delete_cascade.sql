-- Down 0038: put the two self-referencing page foreign keys back as 0001 made them (no action).
set role app_migrator;

alter table app.pages
  drop constraint if exists pages_duplicate_of_page_id_fkey,
  add constraint pages_duplicate_of_page_id_fkey
    foreign key (duplicate_of_page_id) references app.pages (id);

alter table app.pages
  drop constraint if exists pages_superseded_by_page_id_fkey,
  add constraint pages_superseded_by_page_id_fkey
    foreign key (superseded_by_page_id) references app.pages (id);

reset role;
