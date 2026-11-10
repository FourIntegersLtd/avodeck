# 0027 ClamAV as a private Railway service

Date: 2026-10-30 · Status: accepted · Decided by: the founder (plan "lazy-mapping-waterfall", item 2) · Builds on: ADR 0021

**Problem.** `core/scan.py` only knew the recorded stand-in, so production had no real virus scanner and threat T3 (malicious upload) leaned on a control that did not exist.

**Decision.**
- A fourth Railway service, `clamav`, runs the official `clamav/clamav` image (`railway/clamav.Dockerfile`, `railway/clamav.json`), in europe-west4 (Amsterdam) next to the API and worker (ADR 0021). It has **no public domain and no public port**; the API and worker reach it at `clamav.railway.internal:3310` on the private network only.
- It **holds no data at rest**: files are streamed in over `zINSTREAM` and only a verdict comes back. Nothing is written to a volume. Only the signature database is downloaded, by `freshclam` running inside the container.
- `core/scan.py` `ClamdScanner` is a pure asyncio client (no new dependency). `VIRUS_SCANNER=clamd` selects it, with `CLAMD_HOST`, `CLAMD_PORT` (default 3310) and `CLAMD_TIMEOUT_S` (default 60) from the cell config. `recorded` stays for tests and local runs, but a deployed cell can never run it. A deployed cell is detected from `RAILWAY_PROJECT_ID`, which Railway injects into every service (nobody sets it): there the scanner is always ClamAV at the hardcoded `clamav.railway.internal:3310`, timeout 60 s (`CLAMD_HOST`, `CLAMD_PORT`, `CLAMD_TIMEOUT_S` may still override), and `VIRUS_SCANNER=recorded` raises `ConfigError`. The API (at start-up) and the worker (before it opens its database) run the check, so a misconfigured cell stops there. Off Railway (local, tests, CI) nothing needs setting: clamd when `CLAMD_HOST` is set, else the stand-in (audit 12, V1).
- Fail closed: `stream: OK` is clean, `stream: <sig> FOUND` is infected; anything else (unreachable, timeout, `INSTREAM size limit exceeded`, an unknown reply) raises `ScanUnavailable`. A caller must never accept a file on `ScanUnavailable` (failure mode F3).
- Size limits match the largest upload, 500 MB (NFR C1): `StreamMaxLength`, `MaxScanSize` and `MaxFileSize` are 500M and `AlertExceedsMax yes`, so a larger file is blocked rather than passed unscanned.
- Readiness: `ClamdScanner.ping()` sends `zPING` and needs `PONG`. The worker has no ready hook yet, so nothing calls it at start-up; start-up only checks that a scanner is configured, not that it answers.
- The original is served back (`GET /v1/cases/{id}/documents/{id}/file`) only once the scan is recorded clean: 423 `file_not_ready` before a verdict, 409 `file_blocked` if flagged (audit 12, V2b).

**Consequences.**
- One more service to run (memory about 1.5 GB for the signature database). If it is down, uploads wait at "checking" and are not accepted.
- The image tag is pinned in `railway/clamav.Dockerfile`; pin its digest in the Railway build when the first image is chosen.
- Worker and API settings: `VIRUS_SCANNER=clamd`, `CLAMD_HOST=clamav.railway.internal`.
- The service may sleep when idle and wakes when the worker connects (a cold start loads the signature database, about 30 s). `ClamdScanner` retries only the connect, with back-off (2, 4, 8, 15 s, then 15 s), for up to the wake budget `CLAMD_WAKE_S` (default 120 s), then raises `ScanUnavailable` as before. A failure after the stream has started raises at once and the job retries from the start. Still fail closed. Test: `api/tests/unit/test_foundation_clamd_wake.py`.

**Tests.** `api/tests/unit/test_foundation_clamd_scanner.py` (fake clamd), `api/tests/unit/test_foundation_clamd_live.py` (`@pytest.mark.clamd`, a clamav service container in `ci-api.yml`).
