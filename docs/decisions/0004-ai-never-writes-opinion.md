# 0004 The AI finds, links and checks; it never writes opinion

Date: 2026-10-15 · Status: accepted

**Decision.** AI suggests facts, links, flags and comparisons. Every extracted value cites OCR word IDs on a numbered page and is rejected if not found in them. Suggestions are hidden until the expert has read the page (blind read, enforced by the server). Only facts the expert has verified can be cited. No AI output contains generated opinion or rewritten prose. Every model, region and prompt version used is recorded for the expert's methodology appendix.

**Why.** Experts are judged on independence and on having read the records. Published evidence shows LLM citations are often unsupported (SourceCheckup, Nature Communications 2025), so provenance must be enforced in code.
