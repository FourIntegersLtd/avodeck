-- 0224 reader: a second reading records which prompt version made it (audit step 14, R-O-5), so the screen can
-- say the lines were read by the software. A version label, never text from the page.

set role app_migrator;

alter table app.second_readings
  add column prompt_version text check (prompt_version is null or prompt_version ~ '^[0-9A-Za-z._-]{1,40}$');

reset role;
