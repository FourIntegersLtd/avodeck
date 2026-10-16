# 0002 UK-only processing for patient data

Date: 2026-10-15 · Status: accepted

**Decision.** Every service that touches patient records processes it in the UK: Supabase London, DigitalOcean London, Bedrock London **in-region** model IDs (Claude Sonnet 5 / Opus 5.5), Azure UK South (Document Intelligence, Speech). Bedrock `global.*` and `eu.*` inference profiles are denied by IAM policy and by `core/ai/bedrock.py`.

**Why.** Special-category health data; expert and solicitor expectations; simpler DPIA.

**Consequences.** Model choice is limited to what is available in-region in London; re-check when models change. Email (Resend) and e-signature are outside the UK, so they never carry patient content beyond final signed documents.
