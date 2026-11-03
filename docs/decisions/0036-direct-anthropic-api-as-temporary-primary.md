# 0036 The direct Anthropic API as the temporary primary model provider

Date: 2026-11-03 · Status: accepted, temporary · Decided by: the founder · Builds on: ADR 0021, rules 5, 6 and 7 · Sets aside, behind an explicit switch: rule 5 (data stays in the home region)

**Problem.** Bedrock London allows only a handful of requests per minute on our account (`bedrock_rpm_limit`, default 5), which makes a 4,000-page bundle slow to test. The founder's own Anthropic organisation is on the Scale tier (Haiku 5.5: 13,000 requests, 10 million input tokens and 2 million output tokens per minute) and can be used now.

**Decision.** For development and demonstrations on dummy data only, the AI kit asks the Anthropic API directly (`api.anthropic.com`) first and uses `LiveBedrock` as the fallback.

**This breaks rule 5 on purpose.** Prompts leave the UK cell and reach a US-hosted API. It is allowed only because the data is synthetic. Nothing here may be used with a real bundle. Revert before any live case: unset `AI_PRIMARY_PROVIDER` (see below).

**The switch.**
- `AI_PRIMARY_PROVIDER` is `bedrock` (the default, also when unset) or `anthropic`. Any other value raises `ConfigError`.
- `ANTHROPIC_API_KEY` is required with `anthropic`; without it `ConfigError` is raised when settings are read and when the clients are built. The key is never logged.
- With the switch absent, behaviour is exactly as before: no direct client, no direct host on the allow-list, one slot pool, the same limits. Every guard still passes unchanged.
- The model and the endpoint are constants in `core/config.py`, each commented "temporary, dummy data only (ADR 0036)". The direct host is added to `allowed_hosts` only when the provider is `anthropic`.
- At start-up one WARNING states the primary provider and every limit of both providers.

**Model.** `claude-sonnet-4-6` for the `sonnet` role (changed 2026-11-03 from `claude-haiku-5-5`: on the T-07b extraction schema Haiku 5.5 answered `pages` as a string, invented field keys and off-list labels, which rejected whole batches; Sonnet 4.6 gets the shape right and is the same model as the Bedrock fallback; about 4.4k output tokens per 5 pages). An off-list label no longer rejects a batch: `mend_labels` in the kit nulls it where the list allows null, otherwise drops the one nested item (never a whole page); any other fault is still rejected. There is no direct id for the `opus` role, so opus tasks go straight to Bedrock. The direct model is a different build from the one the prompts and baselines were tuned on: `make eval` must pass before it is used for anything beyond throw-away dummy runs. Its tokenizer produces about 30% more tokens for the same text; the cost guard counts the API's own `input_tokens`.

**Request rules.** No `temperature`, `top_p`, `top_k`, `budget_tokens` or prefill is sent (the SDK's `messages` calls have no `temperature` here, and the newer models reject non-default sampling). `thinking` is `{"type": "disabled"}`. The output schema is forced as the only tool. A `refusal` stop reason maps to `ModelRequestRefused`. Every call is streamed (large answers would otherwise be refused by the SDK as too slow for one request); only the final message is used. The SDK runs on `httpx2` and authenticates with `Authorization: Bearer`.

**Prompt caching.** An ephemeral `cache_control` is set on the system block and the tool. A prefix below the model's minimum length is sent whole, with no error. Cache read and creation counts are logged as numbers; the `ai_calls` row is unchanged.

**Limits per provider** (`ProviderLimits`: requests per minute, pages per call, maximum output tokens, concurrency).

| Provider | rpm | pages per call | max output tokens | concurrency |
|---|---|---|---|---|
| anthropic | 1000 | 5 | 8000 | 200 |
| bedrock | `bedrock_rpm_limit` (5) | `extraction_batch_size` (10) | 16000 | 3 |

Slice A reads `kit.primary_limits.pages_per_call` and `.concurrency` for batching and fan-out instead of `settings.extraction_batch_size`.

**Separate slot pools.** `app.ai_request_slots` gains `provider` (migration 0055, default `bedrock`). The kit counts, grants and orders fairly within one provider; the advisory lock key is per provider. A full direct window never holds back Bedrock and the reverse. `app.jobs.last_granted_at` is shared by both providers, which has a minor fairness effect that is accepted.

**Fallback.** A busy direct API (429, 5xx, 529, a dropped connection) is retried on the direct side up to 3 times, waiting `retry-after` (else 2, 4, 8 seconds) but never more than 30 seconds. After that, or at once on an unavailable model (including a spend limit) or a refusal, the same request goes to Bedrock under Bedrock's own back-off, slots and limits. If the request asks for more output than Bedrock can give (`max_tokens` over 16000), the kit raises `TooLargeForFallback(pages_per_call)` so the caller can retry with smaller batches. A wrong answer (schema) is asked again of the same provider and never hides behind the other. With no Bedrock client, a busy or unavailable direct API pauses processing as before.

**What is logged** (always `key=value`; never prompts, context, page text, output, error bodies, `str(exc)` or the key):
- every direct attempt: provider, model, task id, attempt n of 4 (the first call and three retries), milliseconds, input and output tokens, cache read and creation tokens, stop reason, the vendor `request-id`, and outcome (`ok`, `busy`, `unavailable`, `refused`, `invalid_output`, `no_output`);
- every failure: the exception class, the HTTP status, the Anthropic error type and code taken from the typed error, and the retry-after used;
- every retry: the next attempt, the wait and the retry-after;
- every fallback: "direct failed -> bedrock", the reason, the failure's fields, and the size check (`size_check=ok|too_large`, `max_tokens`, `bedrock_max`);
- a rejected answer: provider, model, task and schema attempt;
- slot waits: provider, seconds waited, grants in the last 60 seconds and the limit.

**Database pools.** The founder's Supabase is the free plan (Nano compute): 60 direct connections, 200 pooler clients, shared CPU. The API pool stays at 10 connections. The worker pool (`WORKER_MAX_SIZE` in `core/db.py`) is 20, so a burst of 200 direct calls does not wait for connections; API 10 plus worker 20 leaves room for Supabase's own services. These are our numbers, not Supabase's. The worker pool must be kept within Supabase's Max DB Connections and the pooler's pool size for the `app_worker` role; raise it only with a bigger compute. The queue library's own pool and the reseal command's pool are unchanged.

**Known limitation (found in review, not caused by this change).** The slot counts are read under row-level security, so a job may see only the slot rows of its own case; the per-minute ceiling is then held per case, not account-wide. The vendor's own 429 handling (retry, then the fallback) absorbs the difference. Out of scope here.

**How to turn it off.** Unset `AI_PRIMARY_PROVIDER` (or set it to `bedrock`) and restart. Nothing else changes: the migration is additive and the default provider of every slot row is `bedrock`.

**Consequences.** Faster dummy-data runs. One more third party sees prompts, but only synthetic ones. Remove this ADR's code path, or replace it with an in-region arrangement, before the first real bundle.
