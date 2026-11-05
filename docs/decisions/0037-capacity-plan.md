# 0037 Capacity plan: the numbers Avodeck is built and measured against

Date: 2026-11-05 · Status: accepted · Decided by: the founder · Builds on: ADR 0021 (Railway, no data at rest), ADR 0013 (restricted roles) · See `docs/ops/scalability-plan.md`

**Problem.** The earlier limits (5 concurrent bundle jobs, 200 experts) were guesses made before any load was measured. The audit of 5 November found the first break at one bundle (Bedrock requests per minute) and a hang at about 300 people online (pool of 10, no acquire timeout). We need one set of targets, and a way to see the numbers.

**Decision.** One cell is sized for:
- 1,000 experts registered, 300 online at peak, 50 bundles processing at once (NFR C3 and C5).
- A 4,000-page bundle in 75 minutes or less at p90 (P2). API p95 400 ms or less (P9). Reader page p95 300 ms or less (P3).
- A short job a person is waiting for starts within 30 seconds.

**How we measure it.** `GET /v1/internal/metrics`, behind a bearer token (`OPS_METRICS_TOKEN`), returns plain numbers: pool size, available and waiting; event-loop lag p99; provider 429 counts (Bedrock, direct Anthropic, Azure OCR); jobs queued and the oldest wait per queue; job run time p95 per kind. No ids, names or text (rule 6). Queue and run-time numbers come from two security-definer functions (`app.ops_queue_stats()`, `app.ops_job_seconds()`, migration 0059) so the API role still cannot read other people's rows. Sentry gets one event, `job_dead_lettered` (kind and job id only), when a job ends `failed` for good, and traces a share of requests (`SENTRY_TRACES_SAMPLE_RATE`, default 0.05).

**Alerts.** Interactive queue oldest above 120 s; bulk queue oldest above 900 s; any dead-lettered job; `pool_waiting` above 0 for 60 s (`docs/runbooks/railway.md`).

**Consequences.** Later scalability work (shared slot counts, one Azure limiter, worker memory, queue split, replicas) is judged by these numbers, not by feel. The API and worker keep separate in-process counters: the endpoint shows the API's pool and lag; the worker logs its own snapshot every minute. Raising a target needs a new decision record.
