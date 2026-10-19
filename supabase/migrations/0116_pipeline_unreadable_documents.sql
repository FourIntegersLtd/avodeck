-- 0116 pipeline: a file that could not be opened (F2, failure-modes.md).
--
-- A locked (password-protected) or damaged PDF stops that document only. The split step records why
-- in `unreadable_reason` (a short key, never any text from the file), numbers no pages for it and goes
-- on with the rest. The bundle summary lists it and the instructing-party draft asks for a readable
-- copy. Null for every file that opened. No status changes: the document has no status column.

set role app_migrator;

alter table app.documents
  add column unreadable_reason text check (unreadable_reason in ('locked', 'damaged'));

reset role;
