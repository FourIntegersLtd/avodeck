set role app_migrator;

alter table app.page_analysis
  drop column width,
  drop column height,
  drop column extraction,
  drop column extracted_at;

reset role;
