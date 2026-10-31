-- Down 0126: put the split foreign key back as 0119 made it (no action).
set role app_migrator;

alter table app.pages
  drop constraint if exists pages_split_of_page_id_fkey,
  add constraint pages_split_of_page_id_fkey
    foreign key (split_of_page_id) references app.pages (id);

reset role;
