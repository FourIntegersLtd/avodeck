-- 0147 pipeline: a document over a page cap (audit 13, DP-04).
--
-- The split step refuses a document with more pages than `MAX_DOCUMENT_PAGES`, or one that would take the bundle
-- past `MAX_BUNDLE_PAGES`. Like a locked or damaged file it gets no pages, is not tried again and is listed
-- with the files we could not open; its reason is `too_many_pages` (a short key, never any text from the file).
--
-- The allowed list is every reason any pipeline migration adds (`altered` comes from 0146), so this is correct
-- whether or not 0146 has been applied.

set role app_migrator;

alter table app.documents
  drop constraint documents_unreadable_reason_check,
  add constraint documents_unreadable_reason_check
    check (unreadable_reason in ('locked', 'damaged', 'altered', 'too_many_pages'));

reset role;
