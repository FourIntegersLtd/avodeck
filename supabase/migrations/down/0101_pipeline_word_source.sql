set role app_migrator;

alter table app.ocr_words drop column source;

reset role;
