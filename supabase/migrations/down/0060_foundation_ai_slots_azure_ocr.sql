-- Undo 0060: slots may again be for Bedrock or the direct Anthropic API only.
set role app_migrator;

delete from app.ai_request_slots where provider = 'azure_ocr';
alter table app.ai_request_slots drop constraint if exists ai_request_slots_provider_known;
alter table app.ai_request_slots
  add constraint ai_request_slots_provider_known check (provider in ('bedrock', 'anthropic'));

reset role;
