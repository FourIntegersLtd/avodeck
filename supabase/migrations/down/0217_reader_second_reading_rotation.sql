-- Down 0217: forget how the page was turned when a second reading was asked.
set role app_migrator;
alter table app.second_readings drop column if exists page_rotation;
reset role;
