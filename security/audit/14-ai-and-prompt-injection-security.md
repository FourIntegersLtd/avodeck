# 14. AI, LLM and prompt-injection security

Audit date: 10 November 2026. Branch `audit/ai`, from `main` at `0f8a0478`, with `main` merged in at `adf0657a`.

## Summary

The core design held up. Every model call goes through one path (`AiKit`), which strips identifiers, enforces a cost limit, forces one structured answer and validates it against a closed schema. The model has no tools that act: it cannot read another document, send anything, or change a record. No test found a way for text in a document to make the model's answer reach a report, a verified fact or another case without a code check or the expert in between.

The weaknesses were in what the code did with a model answer after it passed the schema:

- **Model claims stored as checked (high, 6 findings):** in the after-serving and trial slices, a model's statement that a claim was supported, that a sentence came from a source, or that an agenda item was copied word for word was stored without the code comparing it with the page. A dropped "not" could be stored as verbatim.
- **"Did not run" looked like "nothing found" (medium to high, 9 findings):** in six slices, a model call that failed, was paused or returned less than asked left the screen looking clean.
- **No data boundary in the request (medium):** document text was sent with no label or delimiter separating it from instructions.
- **Temporary direct-provider path could be switched on anywhere (medium):** one environment variable sent text to the direct Anthropic API, outside the cell's region, with nothing to stop it in production.
- **Model-calling routes with no limit (medium, 5 findings):** several routes could be called in a loop, each call paying for a model request; one task had no cost guard at all.
- **Smaller gaps (low):** unbounded fields in four output schemas, hidden characters accepted in quoted words, endpoint allow-list edge cases, provider error text kept on exceptions.

Every confirmed weakness was fixed on this branch except the items listed in section 9 as open. What this audit could not settle is how the real models behave: all tests used a scripted stand-in, so the tests prove what the code does with a hostile answer, not how often a real model gives one.

## How this was tested

- 394 new test functions (more cases once parametrised) in `api/tests/security/test_*_ai_*.py`, with helpers in `api/tests/support/ai_injection.py` and `ai_audit.py`.
- Every test asserts the safe behaviour. A weakness was first reproduced as a strict expected failure carrying its id; the marker was removed when the fix made the test pass. No expected failures remain.
- Every "refused" or "dropped" result has a control in which a legitimate answer on the same path is kept.
- The provider is a scripted fake (`ScriptedBedrock`) or a recorded stand-in. No real provider was called. All documents are synthetic and carry a canary string that must never appear in logs, errors or another case.
- Ten injection texts were used in each slice: ignore previous instructions, reveal the system prompt, disclose other documents, change the output, exfiltrate through a tool, steer the classification, suppress relevant records, fabricate missing information, change source references, trigger an unauthorised action.
- The tests ran against a local Postgres (port 58422) with every migration applied.
- An independent reviewer read the tests and fixes for vacuous passes, fixture-caused failures and gaps. Its findings and what was done are in section 12.

## 1. Verified architecture

Read from code, not from documents.

- **One path to a model.** `AiKit.run`, `run_image` and `run_detached` (`api/app/core/ai/kit.py`) are the only callers of a provider client. A guard test scans for any other caller. The order is: cell allow-list for the model id, cost guard, `pii.strip`, build the request, call, validate the answer against the task's JSON schema, one retry on an invalid answer, then `InvalidOutput`. A row goes to `ai_calls` with token counts and no content.
- **Providers found.**
  - AWS Bedrock, `bedrock-runtime` `converse` through boto3 1.43.114. The default. SDK retries off (`total_max_attempts=1`), connect 10 s, read 300 s.
  - Anthropic API direct, `anthropic` 2.4.0 `AsyncAnthropic.messages.stream`. Temporary (ADR 0036). Chosen by `AI_PRIMARY_PROVIDER=anthropic` plus a key. One model only (`claude-sonnet-4-6`, role sonnet). SDK retries off. Logged with region `anthropic-direct`.
  - Azure Document Intelligence for OCR, REST over httpx, api-version 2024-11-30. Not a language model; it receives page images.
  - Speech: a recorded stand-in only. No live client exists in the code.
  - Embedding and reranking: local ONNX models (onnxruntime 1.20.1). Nothing leaves the process.
- **Request shape.** A system prompt from the slice's template, the pack's prompt context as a block labelled reference data, the stripped document text, and one tool (`record_output`) whose input schema is the task's output schema. The model is forced to call it. There are no other tools.
- **Identifier stripping.** `app/core/ai/pii.py` (Presidio 2.2.364, spaCy 3.8.16) removes the names and date of birth in the case identity record and the identifier patterns the pack defines. It runs inside `AiKit.run`, so a caller cannot skip it. Page images sent to `run_image` cannot be stripped; only OCR and the page-sort task use them.
- **Provenance.** `app/core/ai/provenance.py` checks that an extracted value is found in the OCR words the model cited. Only `verified` facts, confirmed by the named expert, can be cited in a report.
- **Worst-case calls per `kit.run`.** Bedrock only, busy: 5. Direct provider then Bedrock: 9. Invalid answer: 2 rounds. Upper bound 18. A job that fails is retried up to 3 times by the queue.

## 2. Data flows

| Integration | Where it runs | Data sent | Data received | Identifiers | Retention at provider |
|---|---|---|---|---|---|
| Bedrock (language model) | Region and endpoint from the cell config; endpoint host must match the region and the allow-list | Stripped document text, pack prompt context, system prompt; page image for the page-sort task | One structured answer | Stripped by `pii.strip` (text). Images are not stripped | Unverified (cloud setting) |
| Anthropic direct (temporary) | `api.anthropic.com`, outside the cell region | The same stripped text; sonnet role only | One structured answer | Stripped by `pii.strip` | Unverified |
| Azure Document Intelligence | Endpoint from the cell config, on the allow-list | Page images (identifiable) | Words with positions | Not stripped: OCR needs the page | Unverified |
| Local embed and rerank | In process | Text | Vectors, scores | Not relevant | None |

Tasks found, by slice: setup T-02, T-04, T-06; pipeline T-07a, T-07b, T-07c; reader T-10; understand T-11, T-13a; write T-14a, T-14b, T-14c, T-15a; report T-17; after serving T-20a, T-20b, T-21a to T-21e; trial T-22a, T-22b, T-22c, T-24. A test (`test_O13e`) fails if the app builds a task the scan does not cover.

Logging: token counts, durations, request ids, the provider's fixed error code. No prompt, answer or document text. Credentials are hidden from `repr` and are never logged. Secrets come from environment variables only.

## 3. Prompt-injection results

The scripted provider plays a model that has been fully taken over: for each of the ten injection texts it returns whatever the injected text asked for. The question tested is what the code then does.

| Injection | Result before fixes | Now |
|---|---|---|
| Ignore previous instructions | No data label or delimiter in the request (P5, P5b, P6b) | Standard data rule on every system prompt; document between marker lines; imitation headers defused |
| Reveal the system prompt | Safe: closed schemas have no field long enough or free enough; nothing echoes the prompt | Safe |
| Disclose other documents | Safe: the model is given one case's text and has no tool to fetch more | Safe |
| Change the output | Schema-invalid answers refused. Schema-valid lies were kept in several slices (sections 4 and 5) | Fixed per slice |
| Exfiltrate through a tool | Safe: one tool, forced, input is the answer; an answer naming another tool is ignored | Safe |
| Steer classification | Page source label from the model was stored as settled (pipeline I-01) | Stored as `suggested`; see open items |
| Suppress relevant records | Missing answers counted as "nothing found" in pipeline, understand, write, after serving, trial | Counted and shown as not checked |
| Fabricate missing information | Values not in the cited words refused. Agenda text, claim support and note sources were not checked | Checked in code against the page words |
| Change source references | Cited word ids outside the page refused. Hidden characters in quotes accepted (R7 to R9) | Refused |
| Trigger an unauthorised action | Safe: no model answer calls a route, changes a status the expert owns, verifies a fact or signs | Safe |

## 4. Output validation

Model output is treated as untrusted in the kit: the schema is closed (`additionalProperties: false`), enums are fixed, and the answer is validated by code, never by a second model call.

What was missing, and is now fixed:

- **Unbounded fields (O12, O13a to O13d).** Four schemas had a string, number or array with no bound. All bounded; a scan test now fails on any new one.
- **Depth and shape (M11, M14).** A malformed 200 reply or a deeply nested answer raised an unhandled error. Now `ModelRequestRefused("malformed_reply")` and a depth cap of 64.
- **Hidden characters (R7, R8, R9).** Zero-width, bidirectional and control characters in a value or quote passed the provenance check. Now refused. Number formats such as `12,5` and `3/4` are matched as whole numbers; a spaced hyphen is not a minus sign.
- **Model text shown to the expert.** Setup titles (O-1) are kept only when made of the question's own words. Pipeline, write and trial free-text fields are rebuilt from the page words or dropped. No screen renders model text as HTML or markdown.

## 5. Clinical and evidential integrity

These are the findings that could change what an expert believes about the records.

| Id | Slice | What happened | Severity |
|---|---|---|---|
| G-I1 | After serving | A claim's "supported" status came from the model alone | High |
| G-I3 | After serving | A quote attributed to a page was stored without being found on that page | High |
| G-O2 | After serving | The model could mark its own claim as checked | High |
| T-01 | Trial | An agenda item with a skipped or reordered word was stored as verbatim | High |
| T-04 | Trial | An empty or partial agenda read looked complete | High |
| T-06 | Trial | A note sentence was marked as coming from a source on the model's word | High |
| D-I1 | Understand | A check that did not run was listed as run with nothing found | Medium-high |
| R-I-6b | Reader | A second reading did not record which prompt version produced it | Medium-high |
| G-I2, G-O1, G-O3, G-I4 | After serving | Missing claims, duplicate claims, wrong page numbers, fewer claims than expected | Medium |
| T-02, T-03, T-07, T-09 | Trial | Item cut mid-sentence; model-chosen item number; missing answers stored as "found nowhere"; line breaks forging request lines | Medium |
| I-1, I-2 | Setup | A question cut mid-sentence or merged across paragraphs stored as asked; duplicates | Medium |
| I-01, I-02, I-03 | Pipeline | Source label stored as settled; partial batch treated as whole; field kept when not on the page | Medium |
| W-I1 to W-I4 | Write | Comparison or lint that did not run shown as clean; citations not re-checked | Medium |
| F-A | Report | A tone check that did not run showed no warnings | Medium |
| D-I2 | Understand | Availability of an entry not stated | Low |

All fixed. The pattern of the fix is the same everywhere: the code compares the model's claim with the page words, and a call that did not run is stored and shown as not run.

Rules 1 to 3 were tested and held throughout: no prompt asks for an opinion, no schema has a free-prose field beyond short labels, and suggestions stay locked until the page is marked read.

## 6. Model and provider security

| Id | What | Severity | Status |
|---|---|---|---|
| M7 | `AI_PRIMARY_PROVIDER=anthropic` worked in any cell, including production | Medium | Fixed: start-up fails in a production cell |
| M10 | T-02 (letter reading) had no case, so no cost guard | Medium | Fixed: per-person daily token allowance and 4 a minute |
| M1 to M6 | Model id matched by prefix; endpoint region not tied to the cell region; allow-list accepted user-info, odd ports, padding and backslashes | Low | Fixed |
| M8, M9 | Direct client read `ANTHROPIC_*` environment variables and followed redirects | Low | Fixed |
| M12, M13 | Provider error bodies kept on the exception chain; unknown error codes logged as sent | Low | Fixed |
| E-LOG1, E-LOG2 | Two log lines could carry a provider message | Low | Fixed |

Held under test: cross-region model ids (`global.`, `eu.`) are refused, look-alike hosts are refused, the direct provider receives only stripped text, credentials do not appear in `repr`, and the canary never reaches a log.

A cell counts as production when `PACKS_APPROVED_ONLY=1` or `SENTRY_ENVIRONMENT` is `production`, `prod` or `live`.

## 7. Request loops

| Id | Route | Fix |
|---|---|---|
| W-E1 | Write: points and lint | 30 model calls a minute per person |
| R-E-2 | Reader: second reading | Limit per page and per person |
| F-B | Report: run final check | One running and one waiting per case |
| T-08 | Trial: agenda, new version, pack check, change item | 30 a minute per person |
| M10 | Setup: letter | Daily allowance and 4 a minute |

## 8. Policy items, with the default taken

You deferred these; each can be changed.

1. **Second reading limits (R-I-5b).** Kept as ADR 0030 sets them; a test now pins the limits.
2. **Direct provider.** Refused only in production-marked cells. Development cells may still use it with dummy data. This fails open: a production cell that sets neither marker would still accept it. A stricter rule (an explicit opt-in flag) would stop the current development API from starting, so it was left for your decision.
3. **Setup titles.** Strict: a model title is kept only when it is an unbroken run of the question's own words, in the question's order. Otherwise the question's first clause is used.
4. **Pipeline field check.** A field is kept when its words are anywhere on the page, not only in the cited words.
5. **Tone warnings (T-17)** never block signing; a tone check that did not run is shown, and does not block either.

## 9. Open items

- **Page source label (pipeline I-01).** The label is now stored as `suggested`, but there is no action yet for the expert to confirm or correct it, and S08 does not show the difference.
- **Third-party names.** `pii.strip` removes the names in the case identity record and pack identifier patterns. Other people's names, addresses and places in the text are sent to the model.
- **Page images.** Images sent for page sorting and OCR cannot be stripped.
- **`claims_expected` shortfall (after serving).** Fewer claims than expected is a notice, not a block. It clears when the expert has confirmed every claim that was found; if none was found it stays.
- **Second readings queued before this branch** have no stored job, so the first repeat request for one makes one extra model call. Two second-reading jobs for one case can, rarely, read the same waiting page twice.
- **Web tests under load.** Four screen tests timed out in a full local run on a busy machine, a different one on each run, none in a file this branch changed.
- **Trial position check.** If the model wrongly says a changed position is consistent, the "needs a note" step is skipped. The expert sees both texts side by side.
- **Flaky test.** `test_pipeline_sort_batch_log.py::test_a_batch_line_has_the_token_fields_and_no_text` can fail when a random case id contains "943". Not caused by this branch.
- **Down files** for 0705 and 0225 were not executed.

## 10. Unverified

These need cloud access or real models, which this audit did not use.

- How real models respond to the injection texts. `make eval` was not run.
- The region a deployed cell really uses, its endpoints, and whether model invocation logging is off in the AWS account.
- Provider retention and training terms for Bedrock, the direct API and Azure.
- Presidio recall on real record text.
- What a real Sentry event contains after scrubbing.
- Whether job-level retries multiply calls beyond the bound in section 1 under a real outage.

## 11. Remediation, in priority order

1. Add the expert's confirm or correct action for the page source label, and show it on S08.
2. Decide whether third-party names should be stripped before the model.
3. Set `AI_PRIMARY_PROVIDER=bedrock` wherever real bundles will be used, then remove the direct path (ADR 0036).
4. Run `make eval` with the ten injection documents added to the synthetic bundles.
5. Check the cloud settings listed under Unverified.

### Fixed on this branch

- Foundation: P5, P5b, P6b, O12, O13a, R7, R8, R9, M1 to M14, E-LOG1, E-LOG2.
- Setup: I-1, I-2, O-1, O13d, the M10 route side, and reading a letter only after a clean virus scan.
- Pipeline: O-01, O-02, O-03, O13b, P-02, I-01 (label marked as suggested), I-02, I-03.
- Reader: R-E-2, R-O-5, R-I-2, R-I-6b.
- Understand: D-I1, D-I2.
- Write: W-E1, W-O1, W-I1, W-I2, W-I3, W-I4.
- Report: F-A, F-B.
- After serving: G-I1, G-I2, G-I3, G-I4, G-O1, G-O2, G-O3.
- Trial: T-01 to T-11.

Migrations added: 0224 and 0225 (reader), 0403 (understand), 0510 and 0511 (write), 0704, 0705 and 0706 (after serving), 0903 (trial). Each has a down file.

## 12. Reviewer pass

An independent reviewer read every new test and fix. It found no remaining expected failures and 16 problems in the first round of fixes. All were fixed test-first except the two marked.

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | `main` had added its own "letter read only after a clean scan" gate to the same functions | High | `main` merged; one gate kept (main's) |
| 2 | The document end marker could be imitated with invisible or full-width characters; no test covered the end marker | Medium | Fixed: look-alikes are folded before the check; 14 cases tested |
| 3 | After serving: one shared word was enough for "matches" | Medium | Fixed: the whole claimed value must be in the cited words |
| 4 | Setup: a title made of the question's words in another order could reverse its meaning | Medium | Fixed: unbroken run in the question's order only |
| 5 | Trial: an agenda item could stop at the end of its first sentence | Medium | Fixed: an item runs to the next item's number or the end |
| 6 | After serving: page footers counted as expected claims, so the notice never cleared | Medium | Fixed: page furniture is not counted; the notice clears when found claims are confirmed |
| 7 | After serving: the expert could set "matches" on a claim with no evidence page | Medium | Fixed: 409 `no_evidence` |
| 8 | Reader: a turned or redrawn page kept its old second reading; another page's job could be returned | Medium | Fixed (migration 0225) |
| 9 | Soft hyphens and zero-width spaces in real text-layer words were refused as hidden characters | Medium | Fixed: these four layout characters are ignored and stripped from stored text; bidirectional, joiner, tag and control characters are still refused |
| 10 | Write: a lint check that did not run looked passed to anyone but the named expert | Low-medium | Fixed (migration 0511) |
| 11 | The production test for the direct provider fails open | Low | Partly: `prod` and `live` now count; fail-closed left as a policy item |
| 12 | The per-person cost guard was only tested against a fake connection | Low | Fixed: tests on the real database, in core and through the route |
| 13 | Report: the control after a finished job only checked for 202 | Low | Not changed: the test harness ends the job before the next request; noted |
| 14 | Setup: the rate-limit row lock was held for the whole model call | Low | Fixed |
| 15 | After serving: a round with no questions asked the model again on every run | Low | Fixed (migration 0706) |
| 16 | Image and detached calls had no guard when there is no case | Low | Fixed: refused before any provider call |

The reviewer found these sound: closed schemas and depth limits, error-code reduction, the endpoint allow-list, model id matching, number matching in provenance, the final-check lock, rate-limit placement after the access check, the write blind-read wait, all migrations (additive, with down files, under existing grants and row-level security), no patient text in new logs or errors, no forbidden pack words, and a cost guard on every model call site.

