-- 0114 pipeline: when a page was received, filed, seen or sent to the clinician (T-07b).
--
-- * `page_analysis.availability_evidence`: a list of `{kind, date, date_text, word_ids}`. The words
--   are on the page; `date` is read by code in the pack's date order, and is null when the printed
--   date could be read more than one way. Storing a page again replaces the list.
-- No new table, so the purge order is unchanged; slice D reads it through
-- `app.core.contracts.availability`.

set role app_migrator;

alter table app.page_analysis
  add column availability_evidence jsonb not null default '[]'::jsonb
    check (jsonb_typeof(availability_evidence) = 'array');

reset role;
