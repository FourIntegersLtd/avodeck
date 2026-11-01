-- 0051 foundation: give app.legal_agreements a created_at column.
--
-- 0039 recorded only accepted_at. The row is always written at the moment of
-- acceptance, but the timestamps-in-UTC guard (architecture 10.9) requires every
-- core table to record its own creation under one of a fixed set of column names.
-- This adds created_at, backfilled from accepted_at for any existing rows.

set role app_migrator;

alter table app.legal_agreements add column created_at timestamptz not null default now();
update app.legal_agreements set created_at = accepted_at;

reset role;
