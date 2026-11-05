# Adding an index to a large cloud database

Migrations run inside a transaction, so `create index concurrently` cannot be used in a migration file. A plain
`create index` blocks writes to the table until it finishes. On a fresh cell that is instant. On a database that
already holds large bundles (`app.pages` can have hundreds of thousands of rows), build the index first without the lock.

1. Connect with the migration role, outside any transaction (psql, or the Supabase SQL editor with a single statement):
   `set role app_migrator; create index concurrently if not exists <name> on app.<table> (...);`
2. Check it is valid: `select indisvalid from pg_index where indexrelid = 'app.<name>'::regclass;` If false, drop it and repeat.
3. Push the migration (`supabase db push --linked`). Its `create index if not exists` is then a no-op.

Migration 0063 (scalability PR 14) needs this on a populated database:

```sql
create index concurrently if not exists jobs_case_created_idx on app.jobs (case_id, created_at desc, id);
create index concurrently if not exists pages_bundle_document_idx on app.pages (bundle_id, document_id);
```
