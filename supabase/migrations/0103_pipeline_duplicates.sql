-- 0103 pipeline: copies set aside and their review (architecture 7, failure mode F13, screen S07).
-- * `page_analysis.typed_words`: how many typed words the page has (pages with few are never copies).
-- * `page_analysis.duplicate_checked_at`: when the page was compared with the case's other pages;
--   each page is compared once, so a repeated job changes nothing.
-- * `page_analysis.duplicate_status`: the review of a page set aside as a copy: `suggested` (by the
--   worker), `confirmed` (the expert agreed), `kept` (the expert undid it; never set aside again).
-- * `duplicate_by`, `duplicate_at`, `duplicate_version`: who reviewed it, when, and the ETag version.
-- The API may change only the review columns, and only with the page action on the case.

set role app_migrator;

alter table app.page_analysis
  add column typed_words integer check (typed_words >= 0),
  add column duplicate_checked_at timestamptz,
  add column duplicate_status text check (duplicate_status in ('suggested', 'confirmed', 'kept')),
  add column duplicate_by uuid references app.people (id),
  add column duplicate_at timestamptz,
  add column duplicate_version integer not null default 0 check (duplicate_version >= 0);

grant update (duplicate_status, duplicate_by, duplicate_at, duplicate_version, updated_at)
  on app.page_analysis to app_api;

create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_case(case_id, 'page.rotate')) with check (app.can_access_case(case_id, 'page.rotate'));

reset role;
