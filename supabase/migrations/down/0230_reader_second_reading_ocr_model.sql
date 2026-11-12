-- Down 0230: removes the OCR model from second readings.
set role app_migrator;

alter table app.second_readings drop column if exists ocr_model_id;

reset role;
