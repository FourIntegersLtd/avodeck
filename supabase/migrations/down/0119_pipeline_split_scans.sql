-- 0119 down: split documents lose their link (the halves stay as pages).

set role app_migrator;

drop index app.documents_split_once;
drop index app.pages_split_of;
alter table app.pages drop column split_of_page_id;
alter table app.documents drop column split_of_page_id;

reset role;
