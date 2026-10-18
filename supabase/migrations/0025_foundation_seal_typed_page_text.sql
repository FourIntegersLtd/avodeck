-- 0025 foundation: the expert's typed-out page text is sealed like every OCR word (rule 6, threat
-- model): `pages.transcribed_by_expert_text` holds the text sealed with the case key
-- (`CaseCrypto.encrypt(..., purpose=core.contracts.pipeline.transcription_purpose(page_id))`), never
-- plain. The name stays, so the state-machine check (0002, `is null`) and the quarantine purge (0109,
-- sets it null) keep working. Plain text cannot be sealed in SQL: any typing held before goes.

set role app_migrator;

alter table app.pages alter column transcribed_by_expert_text type bytea using null;

comment on column app.pages.transcribed_by_expert_text is
  'The expert''s typing of an unreadable page, sealed with the case key (purpose pages/<id>/transcribed_by_expert_text).';

reset role;
