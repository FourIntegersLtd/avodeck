# AWS Bedrock London (eu-west-2): data handling and throughput for Claude (as of 2 November 2026)

Scope: Avodeck calls `anthropic.claude-sonnet-5` in-region in eu-west-2 (10-page calls, ~40k input tokens, 16k max_tokens, ~10k real output), currently throttled with HTTP 429 at about 5 requests per minute. All AWS pages below were fetched on 2026-11-02. Where a number was extracted from a very long page by a summariser rather than read verbatim, it is marked "(extracted via summary)".

## 1. Data privacy: storage, training, sharing, model deployment account

### Takeaway
AWS states Bedrock does not store prompts or completions by default, does not use them to train models, and does not share them with model providers; model providers have no access to the AWS-owned Model Deployment Accounts where the model runs. Abuse detection is automated, and retention modes exist that can change defaults, so the account's retention setting must be checked.

### Cited Findings
- Bedrock's data-protection page states that customer inputs and outputs are not used to train Amazon or third-party models, are not shared with model providers, and that model providers have no access to the Model Deployment Accounts (the AWS-owned accounts that host each provider's model copy). [Data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- Bedrock exposes data retention modes (none, default, aws_review, provider_data_share, inherit); `provider_data_share` is an explicit opt-in to sharing with the provider, so the default is not sharing. [Data retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html)
- Automated abuse detection is listed as supported for the Claude models checked (Sonnet 4.6, Opus 4.6, Opus 5.5 model cards). [Abuse detection](https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html); [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- The Bedrock FAQ repeats the no-training, no-sharing position. [Bedrock FAQs](https://aws.amazon.com/bedrock/faqs/)

### Inferences
- The "zero operator access" framing (nobody at AWS or Anthropic can read content inside the deployment account) matches the architecture described, but I did not find a page that states "zero operator access" in those exact words that I could re-verify after context compaction. Treat the phrase as UNVERIFIED and quote the data-protection page wording instead.
- Anthropic does not receive Avodeck prompts when Bedrock is used, which is the main privacy difference from calling Anthropic's own API.

### Gaps
- Exact retention behaviour of each mode, and whether abuse detection ever stores flagged content for human review: read the data-retention and abuse-detection pages in full before relying on a "nothing is stored" statement in a DPIA. UNVERIFIED in detail.
- Which retention mode is applied to this specific AWS account (account configuration, not documentation).

## 2. Cross-region routing: eu. profiles, global. profiles, and a UK-only profile

### Takeaway
The in-region ID keeps processing in London only. The `eu.` profile can send traffic from London to Frankfurt, Stockholm, Milan, Spain, Ireland, Paris or London, so it can leave the UK; `global.` can go anywhere. No UK-only geographic profile exists. Avodeck's rule of using bare in-region IDs is therefore the only way to keep processing in the UK.

### Cited Findings
- Cross-region inference has In-Region, Geographic (`us.`, `eu.`, `au.`, `jp.`) and Global modes; geographic profiles route within a geography, global routes worldwide. [Cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html); [Geographic CRIS](https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html)
- For Sonnet 4.6, Opus 4.6 and Opus 5.5, an `eu.` request from eu-west-2 can be served in eu-central-1, eu-north-1, eu-south-1, eu-south-2, eu-west-1, eu-west-2 or eu-west-3. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-6.html); [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Geo profile families listed are US, EU, AU, JP (and IN for some models); there is no UK profile. [Inference profiles support](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html); [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html)
- `global.anthropic.claude-sonnet-4-6` lists destinations in the Americas, EMEA (including Tel Aviv, UAE, Bahrain, Cape Town) and Asia Pacific. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html)
- The Opus 5.5 card says in-region inference with the bare model ID is available in eu-west-2 (London) only. [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Sonnet 4.6, Opus 4.6 and Opus 5.5 each show In-Region = yes for eu-west-2; the Sonnet 4.6 and Opus 4.6 cards list the bare ID with In-Region endpoint URL "N/A" in the programmatic-access table while the availability table shows London as In-Region supported (card inconsistency, minor). [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html)
- Sonnet 5 model card and regional availability: see [Sonnet 5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html) and [region availability](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints-region-availability.html).

### Inferences
- Because the `eu.` set includes Frankfurt, Stockholm, Milan, Madrid, Dublin and Paris, using `eu.` profiles means personal data leaves the UK for the EU (adequate under UK GDPR regulations, but it breaks Avodeck's own "home region" rule).
- Cross-region profiles are the standard way AWS raises effective capacity (see section 4), so Avodeck gives up that lever by design.

### Gaps
- Whether "EU geo" processing is contractually described as staying in the EU/EEA for UK GDPR purposes: UNVERIFIED (no AWS page found that speaks to UK transfer treatment of `eu.` profiles specifically).

## 3. AWS DPA, GDPR, UK residency, and Anthropic as sub-processor

### Takeaway
AWS offers a GDPR-compliant DPA including a UK Addendum, and publishes a sub-processor list. I did not find Anthropic on the AWS sub-processor list as a Bedrock sub-processor; AWS positions third-party model providers as not having access to customer content. Confirm with AWS before stating this to a customer.

### Cited Findings
- AWS provides a GDPR Data Processing Addendum (part of the AWS Service Terms) and a UK Addendum for UK GDPR. [AWS GDPR Center](https://aws.amazon.com/compliance/gdpr-center/)
- AWS publishes a sub-processor page for affiliates and third parties that process customer content. [AWS sub-processors](https://aws.amazon.com/compliance/sub-processors/)
- Third-party models are offered "through AWS Marketplace"; the model cards say charges appear on the AWS bill and the Anthropic EULA applies. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html)
- Data stays in the selected region for in-region inference; cross-region profiles may move it (section 2). [Cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)

### Inferences
- Because providers have no access to the deployment account (section 1), AWS treats the provider's role as licensor, not as a processor of customer content; so Anthropic is likely not a sub-processor under the AWS DPA. This is an inference from the architecture, not an AWS statement.

### Gaps
- No direct AWS statement that "Anthropic is / is not a sub-processor for Bedrock". UNVERIFIED. A support case or account-team letter would settle it.
- The AWS sub-processor page was fetched but I did not capture a line-by-line check for every AWS affiliate involved in Bedrock; the earlier search returned only third-party vendor lists. UNVERIFIED beyond the absence of Anthropic.

## 4. Quotas: TPM, RPM, burndown, max_tokens reservation, daily cap, new accounts, increases

### Takeaway
Published default for Sonnet 5 on-demand in eu-west-2 is 3,000,000 tokens per minute with a 10x output burndown, and a shared 700,000,000 tokens-per-day cap. The observed ceiling of about 5 requests per minute is far below what 3M TPM would allow for this workload, so the account's applied quota is probably lower than the published default.

### Cited Findings
- Output burndown rates: 10x for Sonnet 5, Opus 5, Opus 5.5 and Fable 5.1; 15x for 4.8; 5x for 4.7 and below (so 5x for Sonnet 4.6 and Opus 4.6). [Token burndown](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- At admission, Bedrock deducts `total input tokens + max_tokens` from the TPM quota; after the response the unused part is returned. [Token burndown](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- Final consumption is `InputTokenCount + CacheWriteInputTokenCount + OutputTokenCount x burndown`; cache reads are excluded. [Token burndown](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- Default on-demand TPM, each supported Region (extracted via summary): Sonnet 5 3,000,000; Sonnet 4.6 3,000,000; Opus 4.6 1,500,000; Opus 5.5 15,000,000. [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html)
- Default on-demand RPM (extracted via summary): Sonnet 4.6 5,000 and Opus 4.6 5,000; no RPM row found for Sonnet 5 or Opus 5.5. [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html)
- Cross-region TPM defaults (extracted via summary): Sonnet 5 6M, Sonnet 4.6 6M, Opus 4.6 3M, Opus 5.5 30M. [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html)
- "Cross-Model Max Tokens Per Day" is 700,000,000 per account per Region and is not adjustable (extracted via summary). [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html); [Runtime quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html)
- The model cards say default quotas "might be updated depending on regional factors, payment history, fraudulent usage, and/or approval of a quota increase request". [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Quota increases are requested through Service Quotas / AWS Support; see the process page. [Quota increases](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-increase.html)
- Model cards state max output tokens: Sonnet 4.6 64K, Opus 4.6 128K, Opus 5.5 128K. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-6.html); [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)

### Inferences
- Avodeck's call on Sonnet 5 reserves 56k at admission (40k + 16k) and ends at 140k (40k + 10k x 10). At 5 requests per minute that is 280k reserved and 700k final per minute, around a quarter of the published 3M default. So the account's applied TPM is probably well below 3M (new-account or reduced regional default), or an unlisted RPM limit applies. Not verified; check "Applied account-level quota value" in Service Quotas for the Sonnet 5 on-demand TPM and RPM rows.
- Cheap levers: lower `max_tokens` toward real output (reduces admission reservation), turn off or cap adaptive thinking if it inflates output (Sonnet 5 has adaptive thinking on by default), cache the static prompt prefix, move bulk work to Sonnet 4.6 (5x burndown, so 40k + 10k x 5 = 90k per call).
- The daily cap is shared by all models, so splitting across models does not raise the daily ceiling. At 140k per call, 700M tokens a day is about 5,000 calls, which is not a constraint for one 900-page bundle (~90 calls).

### Gaps
- The applied quota for this specific account and the exact reason for the 5 RPM ceiling: UNVERIFIED (account-level data).
- Whether a new account or region gets lower defaults than the published table: the docs say defaults can vary by "regional factors" and "payment history" but give no numbers. UNVERIFIED.
- The 3M figure and RPM rows come from a summariser reading a ~400k-character page; re-check against the live table before quoting.
- No documentation found on turnaround time or approval rules for increases; re:Post discussion (403 on fetch) was not read. UNVERIFIED.

## 5. Per-model quotas

### Takeaway
Quotas are per model and per Region (and separate for on-demand versus cross-region), so Sonnet 5, Sonnet 4.6, Opus 4.6 and Opus 5.5 each have their own TPM and RPM. Only the daily token cap is shared.

### Cited Findings
- The quota table lists separate on-demand and cross-region TPM per model and Region (extracted via summary). [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html); [Runtime quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html)
- The daily cap is "Cross-Model", i.e. shared across models, and not adjustable. [General reference quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html)
- `bedrock-mantle` quotas are separate again, with separate input and output TPM, no RPM and no burndown. [Mantle quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html)

### Inferences
- Fallback routing across Sonnet 5, Sonnet 4.6 and Opus models in London would add independent capacity within the in-region rule.

### Gaps
- Whether the Sonnet 4.6 quota is actually available at its published default on this account: UNVERIFIED.

## 6. Prompt caching and TPM

### Takeaway
Cache-read tokens do not count toward the TPM quota; cache-write tokens do. Sonnet 5 supports explicit caching only.

### Cited Findings
- Cache reads are excluded from quota consumption and cache writes are included. [Token burndown](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- Sonnet 5 uses explicit prompt caching (minimum 1,024 tokens per checkpoint, 4 checkpoints, 5-minute or 1-hour TTL). [Sonnet 5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html); [Prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- Minimum cache checkpoint size differs by model: Sonnet 4.6 1,024; Opus 4.6 4,096; Opus 5.5 512; Opus 5.5 supports explicit caching only (implicit not supported). [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-6.html); [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Pricing page notes Sonnet 5.5 cache reads drop 50% from 7 October 2026 (pricing, not quota). [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)

### Inferences
- Avodeck's 10-page calls share a static system prompt and schema; caching that prefix removes it from final TPM use, but only if it clears 1,024 tokens and calls arrive within the TTL. Because admission still reserves input + max_tokens, caching helps the final tally more than the admission check (the docs imply admission counts total input tokens; not confirmed whether cached input is excluded at admission). UNVERIFIED.
- The page images or page text, which are the bulk of the 40k, change every call and cannot be cached.

### Gaps
- Whether cache-read tokens count at admission time: UNVERIFIED.

## 7. Provisioned Throughput, reserved capacity, service tiers (priority/flex)

### Takeaway
None of the in-region Claude models in London offer Priority or Flex. Reserved is flagged supported for Sonnet 4.6 and Opus 4.6 but not for Sonnet 5 or Opus 5.5, and is arranged through the AWS account team. Classic Provisioned Throughput lists no current Claude model.

### Cited Findings
- Service tiers table: Sonnet 5 Standard only. [Sonnet 5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html)
- Opus 5.5: Standard yes; Priority, Flex, Reserved and Batch all no. [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Sonnet 4.6 and Opus 4.6: Standard yes, Reserved yes, Priority no, Flex no. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-6.html)
- Reserved "provides dedicated throughput with a term commitment", is set at account level, and is enabled by contacting the AWS account team. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Service tiers](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
- Provisioned Throughput supported-models list contains no Sonnet 5, Sonnet 4.6, Opus 4.6 or Opus 5.5; the only Anthropic entries are Claude 3 Haiku and Claude 3 Sonnet in ap-south-1. [Provisioned Throughput supported](https://docs.aws.amazon.com/bedrock/latest/userguide/prov-thru-supported.html); [Provisioned Throughput](https://docs.aws.amazon.com/bedrock/latest/userguide/prov-throughput.html)
- Pricing page: Priority carries a 75% premium and Flex a 50% discount versus Standard, shown for non-Anthropic models; Anthropic Reserved pricing is by contacting the account team. [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)

### Inferences
- The realistic ways to buy more guaranteed Claude capacity in London are a quota increase (section 4) or a Reserved tier agreement on Sonnet 4.6 / Opus 4.6 through the account team; Sonnet 5 has no commitment option today.

### Gaps
- Reserved pricing, minimum term and London capacity for Sonnet 4.6: not published. UNVERIFIED.
- The pricing page was read only to character 100,000 of 118,249.

## 8. Batch inference

### Takeaway
Batch inference is priced about 50% below on-demand but I found no stated completion time and no listing of Sonnet 5 or Opus 5.5 for batch; Sonnet 4.6 is listed in eu-west-2. It suits overnight bulk jobs, not the interactive pipeline.

### Cited Findings
- Pricing page: batch is "50% lower price compared to on-demand inference pricing" for select Anthropic, Meta, Mistral and Amazon models; no completion-time estimate is given. [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)
- Batch inference runs through S3 input/output and the batch-supported models page lists Sonnet 4.6 as single-region in eu-west-2; Sonnet 5 and Opus 5.5 are not listed. [Batch inference](https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference.html); [Batch supported models](https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-supported.html)
- Opus 5.5 card shows Batch = no. [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)

### Inferences
- Batch could absorb non-urgent re-runs or evaluation sets on Sonnet 4.6 in London, but would not help a user waiting for a first pass, and per-job latency is unknown.

### Gaps
- Batch latency / SLA, and whether batch has its own token quota: not found. UNVERIFIED.
- The batch-supported list may be stale for new models (UNVERIFIED); the Sonnet 5 card was not checked for a Batch column.

## 9. bedrock-mantle in eu-west-2

### Takeaway
The Mantle endpoint exists in London, but no Claude model is served In-Region on Mantle there: Sonnet 5 is In-Region on Mantle only in us-east-1, us-gov-west-1, eu-north-1, eu-west-1 and ap-southeast-4, and Opus 5.5 only in us-east-1, ap-southeast-4 and us-gov-west-1. Sonnet 4.6 and Opus 4.6 do not support Mantle at all.

### Cited Findings
- Sonnet 5 Mantle In-Region regions: us-east-1, us-gov-west-1, eu-north-1, eu-west-1, ap-southeast-4 (London absent). [Sonnet 5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html); [Region availability](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints-region-availability.html)
- Opus 5.5 Mantle availability table lists only us-east-1, ap-southeast-4 and us-gov-west-1 as In-Region, with no Geo or Global. [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
- Sonnet 4.6 and Opus 4.6: bedrock-mantle not supported. [Sonnet 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html); [Opus 4.6 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-6.html)
- Mantle quotas: Sonnet 5 3,000,000 input TPM and 300,000 output TPM, no RPM and no burndown. [Mantle quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html)
- On Mantle, Opus 5.5 lacks Guardrails, application inference profiles and invocation logs. [Opus 5.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)

### Inferences
- Mantle would be attractive for Avodeck's output-heavy calls (no 10x burndown: 10k output counts as 10k against a 300k output TPM) but it cannot serve London in-region today, so using it would mean processing in Ireland or Stockholm, which breaks the home-region rule.

### Gaps
- Sonnet 5 Mantle's exact London status over time (new regions may be added): re-check the Sonnet 5 card before any decision. The Sonnet 5 card was read earlier in this session; its London row is as stated in the summary above.
- The 3,000,000 / 300,000 Mantle TPM figures apply "per Region" and I did not confirm they are the defaults for a new account. UNVERIFIED.

## Source-quality notes
- All AWS documentation pages above are primary sources, fetched 2026-11-02. Numeric quota defaults for on-demand rows come from a summariser over a very long page ("extracted via summary").
- The AWS re:Post thread on Sonnet quotas returned HTTP 403; only a search snippet was seen, so it is not used as evidence.
- The pricing page was only read to 100,000 of 118,249 characters; no Sonnet 5 per-token price or in-region versus cross-region premium was found.
- The cause of Avodeck's 5 RPM ceiling is an inference from arithmetic, not a verified finding.
