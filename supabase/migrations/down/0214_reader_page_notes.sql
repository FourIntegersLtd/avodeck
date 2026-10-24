-- 0214 down: the expert's own bookmarks and private notes go (nothing in the record depended on them).

set role app_migrator;

drop table app.page_notes;

reset role;
