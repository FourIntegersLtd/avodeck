# 0021 API and worker run on Railway, Amsterdam

Date: 2026-10-25 · Status: accepted · Decided by: the founder

**Problem.** Rule 5 put the API and worker on DigitalOcean App Platform, London. Avodeck's other products (eventtz, osceguide) already run on Railway with one Docker image and a known runbook. Setting up a second host for one product costs time before any expert has tried Avodeck. Railway has no London region; its only European region is Amsterdam (`europe-west4`).

**Decision.**
- The UK cell's API and worker run on Railway, Amsterdam: one image (`api/Dockerfile`), two services (`api` with the default command, `worker` with `python -m app.worker.main`).
- Everything that stores data stays in the UK: Supabase (database, auth, files) in London, Bedrock London **in-region** model IDs only, Azure Document Intelligence and Speech in UK South. Railway holds no database, volume or file store.
- The web app stays on Vercel and calls the API on its own origin through a rewrite of `/v1/*` (web/AGENTS.md, same-origin rule unchanged).
- Rule 5 now reads: data is stored in the home region; the UK cell's compute may run in the EU (Railway, Amsterdam) under this record. Everything else in rule 5 is unchanged: endpoints and model IDs come from the cell config, never `global.*` or `eu.*` model profiles.

**Why this is acceptable.** Patient data passes through Amsterdam memory in transit between UK services and is never written to disk there. The UK has adequacy regulations for the EEA, so this is not a restricted transfer under UK GDPR. Railway is added as a sub-processor.

**Safeguards.**
- No volumes on either Railway service; logs carry no patient text (rule 6) and are the only thing Railway keeps.
- Database traffic uses the Supabase session pooler with `sslmode=require` (Railway has no outbound IPv6).
- The privacy notice, sub-processor list, DPIA and `docs/security/threat-model.md` name Railway (Netherlands) before any real case data is uploaded.

**Revisit when** Railway opens a London region, a customer contract requires UK-only compute, or the cell moves to DigitalOcean London as first planned. Moving back is a hosting change only: same image, same config.
