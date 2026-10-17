-- 0111 down: parts and exhibits go (documents made from parts stay, with their pages).

set role app_migrator;

drop table app.exhibits;
drop table app.document_parts;
alter table app.document_scans drop column media_type;

reset role;
