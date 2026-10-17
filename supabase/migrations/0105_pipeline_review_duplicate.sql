-- 0105 pipeline: the duplicate review policy checks `page.review_duplicate` (added to the matrix in 0018)
-- instead of `page.rotate`.

set role app_migrator;

drop policy page_analysis_review on app.page_analysis;
create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_case(case_id, 'page.review_duplicate'))
  with check (app.can_access_case(case_id, 'page.review_duplicate'));

reset role;
