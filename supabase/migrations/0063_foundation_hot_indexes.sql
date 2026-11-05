-- 0063 foundation: the two indexes the hot queries were missing (scalability PR 14).
--
-- * app.jobs (case_id, created_at desc, id): the case stream (`core/routes/case_events.py`) reads a case's recent
--   jobs newest first with `order by created_at desc, id limit n`. The index serves the filter and the order, so
--   the stream never sorts a case's whole job history.
-- * app.pages (bundle_id, document_id): the reader and the document routes read one document's pages in a bundle.
--   `pages_bundle` only narrowed to the bundle (up to 4,000 pages).
--
-- Plain CREATE INDEX: the migration runner wraps each file in a transaction, where CONCURRENTLY is not allowed.
-- Both tables are small on a fresh cell, so the brief lock is harmless there. On a cloud database that already holds
-- large bundles, create the same two indexes first from the SQL editor or psql (outside any transaction):
--   create index concurrently if not exists jobs_case_created_idx on app.jobs (case_id, created_at desc, id);
--   create index concurrently if not exists pages_bundle_document_idx on app.pages (bundle_id, document_id);
-- then run this migration; IF NOT EXISTS makes it a no-op. See docs/runbooks/database-indexes.md.

set role app_migrator;

create index if not exists jobs_case_created_idx on app.jobs (case_id, created_at desc, id);
create index if not exists pages_bundle_document_idx on app.pages (bundle_id, document_id);

reset role;
