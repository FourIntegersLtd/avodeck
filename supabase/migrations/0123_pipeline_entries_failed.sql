-- 0123: a page whose entries the model could not read is marked on its own, so it is told apart from
-- a page flagged for another reason (`not_in_words`, `no_words`) that still needs its entries read.

set role app_migrator;

alter table app.page_analysis add column entries_failed_at timestamptz;

reset role;
