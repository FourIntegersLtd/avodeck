set role app_migrator;

drop policy page_analysis_review on app.page_analysis;
revoke update (duplicate_status, duplicate_by, duplicate_at, duplicate_version, updated_at)
  on app.page_analysis from app_api;

alter table app.page_analysis
  drop column typed_words,
  drop column duplicate_checked_at,
  drop column duplicate_status,
  drop column duplicate_by,
  drop column duplicate_at,
  drop column duplicate_version;

reset role;
