-- 0143 pipeline: how big each file inside an email or a zip was (audit 12, V3a).
--
-- The unpack limits (bytes and number of files) hold for one upload as a whole, however deeply its zips and emails
-- are nested. What has already been unpacked from an upload is the sum of `size` over the parts under its original
-- file, so the worker records each part's size when it records the part. Parts made before this column existed have
-- no size and count as zero. Insert only, like the rest of `document_parts`.

set role app_migrator;

alter table app.document_parts add column size bigint check (size is null or size >= 0);

reset role;
