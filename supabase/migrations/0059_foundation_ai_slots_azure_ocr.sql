-- 0059 foundation: Azure OCR submits share the slot scheduler.
--
-- One shared limiter for Azure Document Intelligence (scalability plan PR 4): every page sent to OCR takes a
-- slot with provider `azure_ocr`, counted per second (`AZURE_OCR_TPS`) and in flight (`AZURE_OCR_CONCURRENCY`)
-- across the whole account, through `app.ai_slots_in_use` (0056), whose window argument already takes any
-- interval. Only the provider check needs to know the new name.

set role app_migrator;

alter table app.ai_request_slots drop constraint ai_request_slots_provider_known;
alter table app.ai_request_slots
  add constraint ai_request_slots_provider_known check (provider in ('bedrock', 'anthropic', 'azure_ocr'));

reset role;
