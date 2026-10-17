-- 0105 down: the duplicate review policy goes back to `page.rotate`.

set role app_migrator;

drop policy page_analysis_review on app.page_analysis;
create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_case(case_id, 'page.rotate')) with check (app.can_access_case(case_id, 'page.rotate'));

reset role;
