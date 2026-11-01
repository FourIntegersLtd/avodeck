-- 0222 reader: drop `pages.case_id` itself, now that every policy reading it directly
-- (word_corrections_*, page_turns_*, page_notes_own, fixed by 0221) reads `bundle_id` instead.
-- This is foundation's column, but the drop has to run after 0221, so it lives here rather than
-- in 0044 (foundation can't number a migration higher than reader's own range).

set role app_migrator;

alter table app.pages drop column if exists case_id;

reset role;
