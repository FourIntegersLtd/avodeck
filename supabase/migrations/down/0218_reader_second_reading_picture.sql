-- Down 0218: forget which picture a second reading was asked about.
set role app_migrator;
alter table app.second_readings drop column if exists image_sha256;
reset role;
