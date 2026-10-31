-- Slice B: a second reading also remembers which stored picture the expert asked about (ADR 0030, rule 7).
-- `image_sha256` is the hash of the picture's sealed bytes when asked. The masking job refuses (`page_changed`)
-- if the picture has been redrawn since: the words it would mask from may then be in a different frame.

set role app_migrator;

alter table app.second_readings add column image_sha256 text check (image_sha256 is null or image_sha256 ~ '^[0-9a-f]{64}$');

reset role;
