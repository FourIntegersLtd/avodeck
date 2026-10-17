---
name: add-ai-task
description: Add or change an AI task (Claude, Azure OCR or Speech) in Avodeck safely: identifier stripping, schema-constrained output, provenance check, logging, fixtures and evaluation.
---

# Add an AI task

AI suggests; the expert decides. The AI never writes opinion.

1. **Spec:** find the task in `docs/architecture.md` section 6.3 (task ID, model, input, output fields, check). New tasks need a task ID and a row added there first.
2. **Fixture first (red):**
   - Add the synthetic input to `api/tests/fixtures/<task_id>/input.json` (OCR words with IDs).
   - Add the expected output to `.../expected.json`, including `word_ids` for every value.
   - Record or hand-write the model response to `.../response.json`.
   - Write tests: correct mapping; values not in cited words are rejected; malformed output triggers one retry and then `needs_human`; no identifiers reach the model.
3. **Schema:** a Pydantic model for the output. Every extracted value has `word_ids: list[str]`. Only short `reason` strings as free text; no prose, summaries or opinions.
4. **Jurisdiction hints:** record-system examples and terminology come from the case's specialty pack (`records.yaml`, `facts.yaml`, `vocabulary.yaml`), injected into the prompt; never hard-code EMIS/SystmOne or UK terms.
5. **Prompt:** add it under `api/app/core/ai/prompts/<task_id>.md` with a version line (`version: 2026-10-15.1`). Keep a stable system prompt at the top so it is cached.
6. **Call through the AI kit only:**
   ```python
   stripped = pii.strip(payload, case_names=case.name_list)
   result = await ai.extract(task_id, model="sonnet", schema=Out, payload=stripped, case_id=case.id)
   checked = provenance.check(result, ocr_words)
   ```
   Use `model="opus"` only for tasks listed as M4 in section 6.1.
7. **Settings:** temperature 0; structured output via tool schema; batch for pipeline tasks; London in-region model IDs only (enforced in `bedrock.py`).
8. **Never** log prompts, inputs or outputs. `ai_calls` stores task, model, region, prompt version and tokens only.
9. **Evaluation:** add a case to `evals/<jurisdiction>/<case-type>/<specialty>/expected/` and run `make eval`. A prompt or model change must not lower precision below the threshold.
10. **Show suggestions as "Please check"** until the expert verifies them; only verified items can be cited.
