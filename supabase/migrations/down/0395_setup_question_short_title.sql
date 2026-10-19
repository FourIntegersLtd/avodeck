-- Down for 0395 setup: drops the question short title.
set role app_migrator;
alter table app.questions drop column if exists short_title;
reset role;
