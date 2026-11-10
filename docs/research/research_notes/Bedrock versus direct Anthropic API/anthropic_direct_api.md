# Anthropic first-party Claude API: data-protection terms, data residency, rate limits (as of 2 Nov 2026)

Method note: primary pages were fetched on 2 Nov 2026 through a summarising fetch tool. Where the tool paraphrased rather than quoted (the DPA, the BAA article), this is flagged. Page dates are given where the page shows one. The old docs.claude.com URLs now 301/302 to platform.claude.com.

## 1. DPA: SCCs, UK Addendum, transfer impact assessment help

### Takeaway
CONFIRMED. The Anthropic DPA is incorporated into the Commercial Terms. It incorporates SCC Modules 2 (controller to processor) and 3 (processor to processor), incorporates the UK Addendum, and commits Anthropic to supply information for a transfer impact assessment on request. The DPA version fetched is effective 24 Feb 2025, so it is not new. The DPA page does not say where Customer Data is stored.

### Cited Findings
- CONFIRMED. SCC Modules Two and/or Three are incorporated "to the extent required by Applicable Data Protection Laws" (DPA Section I.1, completed in Schedule 3). The DPA page is effective 24 Feb 2025. — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- CONFIRMED. The UK Addendum (the ICO International Data Transfer Addendum, s119A DPA 2018) "will be incorporated into and form part of this DPA" where required by law (Schedule 3, B.1.a). The Customer is the data exporter and Anthropic the importer. — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- CONFIRMED. On request Anthropic will provide information "reasonably necessary for Customer to complete a transfer impact assessment" where required by law (DPA Section I.4). — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- CONFIRMED. The Commercial Terms say data submitted through the Services is processed under the Anthropic DPA, which is "incorporated into these Terms by reference" (Section C). — [Commercial Terms](https://www.anthropic.com/legal/commercial-terms)
- CONFIRMED. The privacy center says the DPA "with Standard Contractual Clauses (SCCs)" is "automatically incorporated" into the Commercial Terms, so no separate signature is needed. Page dated 16 Mar 2026. It does not mention the UK Addendum. — [Privacy centre: view the DPA](https://privacy.claude.com/en/articles/7996862-i-m-a-commercial-customer-how-do-i-view-your-data-processing-addendum-dpa)
- Other DPA terms (paraphrased by the fetch tool): general authorisation of the Schedule 4 subprocessors, with notice of new ones and a 15-day written objection window (Section C.1 and C.3). Return or deletion of Customer Data within 30 days of termination, except where law, a dispute or abuse-prevention requires keeping it (Section H). Annual external audits, with SOC 2 reports on written request (Section F). — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- Secondary only: a law-firm tool reads the DPA as covering Module 2 or 3 plus a Swiss adaptation. Not primary. — [compound.law](https://compound.law/en-DE/tools/anthropic-scc/)
- The fetch tool reported that in UK Addendum Table 4, "Anthropic (as data importer) may end the Approved Addendum". This is a paraphrase and should be checked against the DPA text before anyone relies on it. — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)

### Inferences
- The DPA is click-through and non-negotiated by default. Anything bespoke (health-data wording, UK-only processing) would need a negotiated agreement with sales.
- For a UK exporter, "the Customer is the exporter" means Avodeck must run its own transfer risk assessment. Anthropic only provides information for it.

### Gaps
- The DPA page does not state storage or processing locations. Schedule 4 points to an online subprocessor list that was not fetched, so the subprocessor entities and countries are UNVERIFIED.
- Table 3 of the UK Addendum was not shown by the fetch tool.

## 2. Special categories "None", health data, BAA/HIPAA

### Takeaway
CONFIRMED on the wording: Schedule 1, B.3 reads "Special categories of personal data (if applicable): None." Nothing found says this can be amended by a BAA or a healthcare addendum. A BAA is a US HIPAA instrument and is a separate document. Anthropic does offer one for the first-party API, but the Batch API and Files API are excluded.

### Cited Findings
- CONFIRMED. Schedule 1, Part B.3 of the DPA has the row "Special categories of personal data (if applicable)" with the value "None." Categories of personal data and data subjects are "Determined by the Customer". The DPA has no separate section on customer obligations for sensitive data. — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- CONFIRMED. The Commercial Terms contain no clause on health data, HIPAA or BAAs. Section D.3 makes the customer responsible for judging whether Outputs suit its use case, "including where human review is appropriate". — [Commercial Terms](https://www.anthropic.com/legal/commercial-terms)
- CONFIRMED. Anthropic offers a BAA for "HIPAA-ready services, such as use of our first-party API" (also Claude in Microsoft Foundry and Enterprise plans). For the first-party API the organisation's Primary Owner signs the BAA, then contacts Anthropic or sales to enable it. The BAA covers only the single organisation that accepted it. — [Privacy centre: BAA](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) (no dated "updated" line; it says "Updated this week")
- CONFIRMED. Per the docs, eligible organisations can review and execute Anthropic's standard BAA in the Console (Data retention page), or ask sales for a negotiated BAA. Once enabled, HIPAA readiness is permanent for that organisation. It is enforced per organisation: non-eligible features return HTTP 400, and the docs advise a separate organisation for non-HIPAA workloads. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- CONFIRMED. HIPAA readiness does not cover the Console UI, Claude Platform on AWS, Claude Code or beta features. The BAA article also lists the Batch API, Files API, Skills API, Code Execution and Web Fetch as not covered. Messages API, PDF input (inline), prompt caching, structured outputs, fast mode and the 1M context window are HIPAA-eligible. `inference_geo` is HIPAA-eligible. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention); [BAA article](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers)
- CONFIRMED. PHI guidance: "Do not include PHI in JSON schema definitions." This covers property names, `enum` values, `const` values and `pattern` regexes. Patient-specific information belongs only in message content. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- UNVERIFIED. I found no source saying the DPA special-categories row can be amended by a BAA, a "healthcare addendum" or otherwise. No UK or GDPR-specific healthcare addendum was found.

### Inferences
- A BAA addresses US HIPAA only. It does not change the GDPR or UK GDPR paperwork. For UK special-category health data, Avodeck should ask Anthropic in writing to amend Schedule 1 B.3 to list health data. That is a negotiation point, not a documented option.
- SCC Annex I.B normally requires the sensitive data and its safeguards to be described. This is general knowledge, not from the fetched pages. A "None" entry sits badly with a workload that is mostly health data.
- Avodeck strips identifiers but the text still contains health data. Treat it as special-category data for pseudonymised data.

### Gaps
- The text of the standard BAA and the HIPAA Implementation Guide (behind the Console or Trust Center) was not retrieved.
- The fetch tool could not give the DPA text verbatim past 125-character quotes.

## 3. Standard retention, exceptions, and the tension between two Anthropic pages

### Takeaway
CONFIRMED with a flag. The privacy center (updated 1 Jul 2026) says API inputs and outputs are deleted within 30 days, with exceptions. The platform docs now say conversation content is "not retained by default" for the API (except Covered Models, which are Fable/Mythos and not Sonnet 5). The two pages are not obviously consistent.

### Cited Findings
- CONFIRMED. "We automatically delete inputs and outputs on our backend within 30 days of receipt or generation." Last updated 1 Jul 2026. — [Privacy centre: retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
- CONFIRMED. Exceptions: when automated systems flag a Usage Policy violation, inputs and outputs may be kept up to 2 years and trust-and-safety classification scores up to 7 years. Also data required by law, data in services with extended retention (e.g. Files API), data under a separate agreement (e.g. ZDR), and feedback data (5 years). — [Privacy centre: retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
- CONFIRMED. Even with ZDR or HIPAA, Anthropic may retain data "where required by law or where it has been flagged by Anthropic's automated trust and safety systems", for up to 2 years. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- CONTRADICTED / needs reconciling. The docs "How Anthropic approaches data retention" says "Conversation content (your prompts and Claude's outputs) is not retained by default; the exception is Covered Models, which require 30-day retention". Covered Models are Claude Fable 5.1, Mythos 5.1, Fable 5 and Mythos 5 only. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) vs [Privacy centre: retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
- Covered Models require 30-day retention and are not available under ZDR unless Anthropic authorises it. Sonnet 5 and 5.5 are not on that list. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)

### Inferences
- The safest reading for a contract or DPIA is the more conservative one: up to 30 days plus flagged-content retention of up to 2 years. Ask Anthropic which statement governs the API, and get it in writing.

### Gaps
- I could not determine which page governs for Sonnet 5 on the standard (non-ZDR) API.

## 4. Zero Data Retention (ZDR) and structured outputs

### Takeaway
CONFIRMED. ZDR is by arrangement through sales and is enabled per organisation. Structured outputs and strict tool use are "Yes (qualified)" for ZDR: prompts and outputs are not stored, but the JSON schema is cached for up to 24 hours since last use. They are also HIPAA-eligible. The advice not to put PHI in the schema is CONFIRMED.

### Cited Findings
- CONFIRMED. Under ZDR, Anthropic "does not store customer prompts or responses at rest after the API response is returned". To request it, contact sales. It is enabled per organisation, and each new organisation needs separate enablement. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- CONFIRMED. Structured outputs (`/v1/messages`): ZDR "Yes (qualified)", HIPAA "Yes". "Only the JSON schema is cached, for up to 24 hours since last use. This also covers strict tool use (`strict: true`...)". — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention); [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) ("Compiled grammars are cached for 24 hours from last use")
- CONFIRMED. Sonnet 5 and Sonnet 5.5 support structured outputs. Limits: 20 strict tools per request, 24 optional parameters, 16 union-type parameters, and a 180 s compile timeout (400 "Schema is too complex" if exceeded). — [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- ZDR eligible without qualification: prompt caching (KV cache and hashes held in memory for the cache TTL), PDF support, fast mode, web search (non-dynamic-filtering), the 1M context window. Not ZDR eligible: code execution, programmatic tool calling, Batch API, Files API, message threads, user profiles, Managed Agents (nothing eligible), Claude for Excel. The API does not block "No" features under ZDR. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- ZDR excludes Covered Models. Data residency (`inference_geo`) is ZDR-eligible. CORS is not supported for ZDR organisations, so the API must be called from a backend. — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention); [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency)

### Inferences
- Avodeck's extraction schemas must hold only static field names and enums, never patient-derived values. That fits rule 7 style schema design. The Batch API is not ZDR- or HIPAA-eligible, so a "cheap batch" route conflicts with a ZDR posture.

### Gaps
- Whether ZDR is available to a small UK customer and on what commercial terms: not stated publicly. Sales-gated.

## 5. Training on API/commercial data

### Takeaway
CONFIRMED. Anthropic does not train on commercial or API data by default.

### Cited Findings
- CONFIRMED. "By default, we will not use your inputs or outputs from our commercial products" to train models. Last updated 18 Aug 2026. Training can occur via feedback or an explicit opt-in. — [Privacy centre: model training](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training)
- CONFIRMED. Contract text: "Anthropic may not train models on Customer Content from Services." Customer retains rights to Inputs and owns Outputs (Section B). The wording is "may not", not "will not". — [Commercial Terms](https://www.anthropic.com/legal/commercial-terms)
- The docs also say retained data "is never used for model training without your express permission". — [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)

### Inferences
- The feedback and bug-report path is the one leak. Avodeck should never forward end-user feedback containing record text to Anthropic.

### Gaps
- None material.

## 6. Data residency / inference geography

### Takeaway
CONTRADICTED for any UK or EU claim. As of 2 Nov 2026 the first-party API's `inference_geo` accepts only `"global"` (default) and `"us"`. Workspace geo (storage at rest) is `"us"` only. There is no EU-only or UK-only inference or storage on the direct API.

### Cited Findings
- CONFIRMED. `inference_geo` values: `"global"` (default, "may run in any available geography") and `"us"` ("US-based infrastructure" only). Supported on Claude 4.6 and later models including Sonnet 5 and 5.5; older models return 400. — [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency)
- CONFIRMED. "Current limitations: Inference geo: Only 'us' and 'global' are available. Workspace geo: Only 'us' is currently available" and it cannot be changed after workspace creation. Workspace geo governs where data is stored at rest and where endpoint processing happens. — [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency)
- CONFIRMED. `us` inference costs 1.1x standard on 4.6+ models. Workspace-level `allowed_inference_geos` and `default_inference_geo` exist. Rate limits are shared across geos. — [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency); [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- CONFIRMED. On Amazon Bedrock the inference region is set by the endpoint or inference profile, so `inference_geo` does not apply there. — [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency)
- CONFIRMED. Anthropic's privacy policy (effective 10 Sep 2026) says personal data is transferred to servers "in the US, or to other countries outside the European Economic Area", and relies on adequacy decisions and SCCs. — [Privacy Policy](https://www.anthropic.com/legal/privacy)
- Third-party searches found no EU or UK `inference_geo` announcement. These are secondary (blogs) and a Microsoft Foundry EU timeline was not verifiable. — e.g. [Sonomos](https://sonomos.ai/blog/claude-eu-data-residency-2026/)

### Inferences
- Direct API means UK special-category data goes to the US at rest and in inference (or global). That is a restricted transfer needing SCCs and the UK Addendum plus a risk assessment. It cannot satisfy a "data stays in the UK/home region" rule. Avodeck's AGENTS.md rule 5 would be broken, and a docs change or ADR would be needed.

### Gaps
- Any unannounced UK/EU roadmap: not found. The subprocessor list (where storage actually sits) was not fetched.

## 7. EU-US Data Privacy Framework and UK Extension

### Takeaway
UNVERIFIED as primary, but the evidence leans CONTRADICTED. I could not query the live registry, since it is JavaScript-rendered and returned no results through my tools. Two registry-based secondary reports (Aug and Sep 2026) say Anthropic is not on it, and Anthropic's own documents cite SCCs only.

### Cited Findings
- Anthropic's Privacy Policy (10 Sep 2026) and DPA mention adequacy decisions and SCCs but not the DPF or UK Extension. — [Privacy Policy](https://www.anthropic.com/legal/privacy); [DPA](https://www.anthropic.com/legal/data-processing-addendum)
- The privacy centre GDPR article (16 Mar 2026) mentions the DPA but not the DPF, UK Extension or SCCs. — [Privacy centre: GDPR](https://privacy.claude.com/en/articles/10015887-how-do-you-handle-gdpr-or-related-issues)
- Secondary: a compliance review says the registry was searched on 8 Sep 2026 (active and inactive) and returned nothing for Anthropic. — [Anthropic DPA, GDPR & AI Act review (companyscope)](https://companyscope.io/vendors/anthropic) (as quoted in search results; page not fetched)
- Secondary: an author says they "checked the official register in August" and OpenAI and Anthropic returned nothing, while the Microsoft and Google controls did. — [Compliance Engineering Substack](https://complianceengineering.substack.com/p/openai-and-anthropic-are-not-on-the)
- Conflicting secondary: a Sonomos post says Anthropic participates in the DPF but cites no registry entry. — [Sonomos](https://sonomos.ai/blog/is-claude-gdpr-compliant-2026/) (search snippet only)

### Inferences
- Do not assume DPF cover. Plan on SCCs and the UK Addendum, which is what the DPA provides. Check the register by hand at dataprivacyframework.gov/s/participant-search (search "Anthropic", include inactive) before relying on either answer.

### Gaps
- Direct registry confirmation was not obtainable with my tools.

## 8. Rate limits (direct API) and fit to the Avodeck workload

### Takeaway
CONFIRMED with corrections. Tiers are now named Start, Build, Scale, Custom (plus an Evaluation tier for new organisations). Cached reads do not count toward ITPM for Sonnet. Priority Tier is no longer for sale and does not support Sonnet 5 or 5.5. A ~900-page job (about 3.6M input and 0.9M output tokens) fits inside Start-tier token limits over 5 minutes, but not in one minute.

### Cited Findings
- CONFIRMED. Sonnet 5 limits (organisation-level, per minute): Start 1,000 RPM / 2,000,000 ITPM / 400,000 OTPM. Build 5,000 / 5,000,000 / 1,000,000. Scale 10,000 / 10,000,000 / 2,000,000. Custom: contact sales via the Console Rate limits page. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- Sonnet 5.5 (separate bucket): Start 4,000 RPM / 2M / 400k. Build 10,000 / 5M / 1M. Scale 13,000 / 10M / 2M. Sonnet 5 and 5.5 each have their own limits. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- CONFIRMED. "Only uncached input tokens count toward your ITPM": `input_tokens` and `cache_creation_input_tokens` count, `cache_read_input_tokens` does not (Haiku 3.5 is the exception). OTPM counts actual generated tokens; `max_tokens` does not factor in. Token-bucket algorithm. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- Monthly spend caps: Start $500, Build $1,000, Scale $200,000; Custom has none. At the cap, requests return 429 `enforced_spend_limit_reached` with no `retry-after`, until 00:00 UTC on the 1st. New or low-history organisations may start in a lower Evaluation tier. Tier is assigned automatically from usage history and account standing. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- Requesting more: use "Request tier increase" in the Console Rate limits page, or contact support or sales. Acceleration limits can return 429s on sharp usage jumps. Workspace-level limits can be set lower. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- CONTRADICTED (claim of Priority Tier as a route). "Service tiers are legacy." Priority Tier capacity commitments are "no longer available for purchase". It is not supported for Sonnet 5, Sonnet 5.5, Opus 5/5.5 or Haiku 5.5. For guaranteed capacity "contact sales". — [Service tiers](https://platform.claude.com/docs/en/api/service-tiers)
- Batch API has separate limits (Start 1,000 RPM, 200,000 requests in queue). The Batch API is not ZDR- or HIPAA-eligible. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits); [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)

### Inferences
- Workload arithmetic (mine, not Anthropic's): 900 pages x 4k = 3.6M input tokens and 900 x 1k = 0.9M output tokens. Over 5 minutes that is 720k ITPM, 180k OTPM and 180 RPM at one call per page. Start tier on Sonnet 5 (2M / 400k / 1,000) covers this with headroom. A job finished in 1 minute would need 3.6M ITPM (above Start, within Build) and 900k OTPM (above Start, within Build's 1M).
- A 4,000-page bundle (about 4.4x) over 5 minutes is about 3.2M ITPM and 800k OTPM. That exceeds Start and fits Build. Several experts running at once would sum on one organisation-level pool.
- A shared prompt and schema prefix cached with prompt caching would cut counted ITPM. This is optional because the stated example already fits.
- Practical risk is the spend cap: Start $500 and Build $1,000 per month could be exhausted by a handful of large bundles. Pricing was not fetched, so this is unquantified. Real capacity risk is "best-effort" availability and "server overloaded" errors without Priority Tier.

### Gaps
- Tier qualification criteria (deposit amounts and time thresholds) are not shown on the current rate-limits page. The Console shows the tier.
- Per-token pricing for Sonnet 5 / 5.5 was not retrieved.
- The page does not state how quickly sales grants Custom limits.

## 9. Healthcare statements and Usage Policy restrictions

### Takeaway
CONFIRMED that Anthropic markets Claude for Healthcare as "HIPAA-ready" (11 Jan 2026) and has a first-party-API BAA. The Usage Policy fetched is effective 12 Nov 2026, which is after today's date, so the in-force version may differ. It treats medical and legal decisions as "High-risk" and requires human review and disclosure.

### Cited Findings
- CONFIRMED. Claude for Healthcare was announced 11 Jan 2026. Anthropic says it offers "HIPAA-ready products" and "We do not use users' health data to train models." The page does not mention a BAA. A 7 Feb 2026 changelog narrowed which products count as HIPAA-ready. — [Claude for Healthcare](https://www.anthropic.com/news/healthcare-life-sciences)
- Usage Policy shown as "effective Nov 12, 2026". Treat as forthcoming and re-check when it takes effect. — [Usage Policy](https://www.anthropic.com/legal/aup)
- Medical is a High-risk Area: "diagnosing a condition; recommending, starting, stopping, or adjusting a treatment or medication, including dose". Insurance medical-necessity determinations are also High-risk. — [Usage Policy](https://www.anthropic.com/legal/aup)
- Legal is High-risk: "advising an individual on their legal rights, obligations, or liability", "preparing legal documents for them to sign or file", and "deciding or recommending the outcome of a civil, administrative, or arbitral matter". — [Usage Policy](https://www.anthropic.com/legal/aup)
- High-risk requirements: "A qualified person must meaningfully review a High-risk AI Recommendation, with the authority to change or modify it". Affected individuals "must be clearly told that AI was used". Users do not need to name Anthropic or Claude. — [Usage Policy](https://www.anthropic.com/legal/aup)
- Privacy: users may not "misuse, collect, solicit, or gain access without permission to private information" such as health data. — [Usage Policy](https://www.anthropic.com/legal/aup)
- Commercial Terms Section D.2 makes the Usage Policy binding. — [Commercial Terms](https://www.anthropic.com/legal/commercial-terms)

### Inferences
- Avodeck's fact extraction with a named human expert reviewing every page is probably outside "diagnosing" and "recommending treatment", and it keeps a qualified-human-review pattern. This is my reading, not an Anthropic statement. Whether litigation support counts as "preparing legal documents" is untested. A written confirmation from Anthropic would be prudent. The disclosure requirement (told AI was used) could matter for reports served in court.
- Processing a third party's health records (claimants) raises the "without permission" language. A lawful basis and instruction chain should be documented.

### Gaps
- The Usage Policy version in force today (before 12 Nov 2026) was not retrieved.
- No Anthropic statement specific to medico-legal or expert-witness use was found.

## 10. Apple (minor)

### Takeaway
Both CONFIRMED, with a nuance on the ChatGPT one.

### Cited Findings
- CONFIRMED. Apple says Private Cloud Compute aims for "a strong form of stateless data processing where personal data leaves no trace in the PCC system". Data must not be retained "including via logging or for debugging, after the response is returned", and "no user data is retained in any form after the response is returned". — [Apple Security Research: PCC](https://security.apple.com/blog/private-cloud-compute/)
- CONFIRMED. Without a ChatGPT account (or not signed in), OpenAI must process data only to fulfil the request and "not store your information or any responses it provides unless required under applicable laws", must not use it to train, and the IP address is obscured. When signed in, OpenAI's account settings and policies apply and "OpenAI may log your request... and use this data to train or improve their models". — [Apple: ChatGPT extension privacy](https://www.apple.com/legal/privacy/data/en/chatgpt-extension/)

### Inferences
- The stateless claim is a design goal Apple verifies with published components. The ChatGPT claim is a contractual commitment by OpenAI as described by Apple, and carries a legal-compulsion carve-out. Neither applies to Avodeck's API choices.

### Gaps
- The date of each Apple page was not retrieved.
