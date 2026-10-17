-- 0102 pipeline: per-page extraction (T-07b, architecture 6.2 and 7 step 4).
-- * `page_analysis.width`, `height`: the page's size in the units its word polygons use, so a
--   fact's box can be given as a share of the page.
-- * `page_analysis.extraction`: the model's checked answer for the page, sealed with the case key
--   (it quotes page text), kept for the date, gap and missing-document steps.
-- * `page_analysis.extracted_at`: when the page's facts were stored; a page is extracted once.

set role app_migrator;

alter table app.page_analysis
  add column width real check (width > 0),
  add column height real check (height > 0),
  add column extraction bytea,
  add column extracted_at timestamptz;

reset role;
