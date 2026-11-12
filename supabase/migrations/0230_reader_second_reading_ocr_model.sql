-- 0230 reader: a second reading says which OCR model read the page again (audit PV-18).
-- Only a page the expert typed out is read again with OCR for its boxes; the other pages use the stored words and
-- leave this empty. The vision model's id is on the `ai_calls` row of the call itself.

set role app_migrator;

alter table app.second_readings add column ocr_model_id text
  check (ocr_model_id is null or ocr_model_id ~ '^[A-Za-z0-9._:/-]{1,100}$');

reset role;
