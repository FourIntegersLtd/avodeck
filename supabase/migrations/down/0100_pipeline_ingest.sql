set role app_migrator;

drop table app.ocr_words;
drop table app.page_analysis;
drop table app.document_scans;
drop function app.pipeline_same_case_page();

reset role;
