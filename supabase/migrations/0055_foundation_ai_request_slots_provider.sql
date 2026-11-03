-- 0055 foundation: each model provider gets its own slot pool in `ai_request_slots`.
--
-- TEMPORARY, dummy data only (ADR 0036): with `AI_PRIMARY_PROVIDER=anthropic` the AI kit calls the direct
-- Anthropic API first and Bedrock as the fallback. Each has its own requests-per-minute ceiling
-- (`AiSettings.limits`), so the two never share a pool: a slot row says which provider it is for, and
-- `app.core.ai.kit` counts, grants and orders fairly within one provider at a time.
-- Every existing row is a Bedrock row, which is also the default, so Bedrock-only behaviour is unchanged.

set role app_migrator;

alter table app.ai_request_slots
  add column provider text not null default 'bedrock',
  add constraint ai_request_slots_provider_known check (provider in ('bedrock', 'anthropic'));

create index ai_request_slots_provider_granted on app.ai_request_slots (provider, granted_at);

reset role;
