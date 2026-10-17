-- 0101 pipeline: where each word came from (architecture 4.1 `ocr_words.source`, 7 step 2):
-- the page's own text layer (`embedded`), OCR (`ocr`) or the expert's transcription (`expert`).

set role app_migrator;

alter table app.ocr_words add column source text not null default 'ocr'
  check (source in ('ocr', 'embedded', 'expert'));
alter table app.ocr_words alter column source drop default;

reset role;
