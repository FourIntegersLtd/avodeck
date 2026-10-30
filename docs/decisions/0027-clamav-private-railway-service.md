# 0027 ClamAV as a private Railway service

Date: 2026-10-30 · Status: accepted · Decided by: the founder (plan "lazy-mapping-waterfall", item 2) · Builds on: ADR 0021

**Problem.** `core/scan.py` only knew the recorded stand-in, so production had no real virus scanner and threat T3 (malicious upload) leaned on a control that did not exist.

**Decision.**
- A fourth Railway service, `clamav`, runs the official `clamav/clamav` image (`railway/clamav.Dockerfile`, `railway/clamav.json`), in europe-west4 (Amsterdam) next to the API and worker (ADR 0021). It has **no public domain and no public port**; the API and worker reach it at `clamav.railway.internal:3310` on the private network only.
- It **holds no data at rest**: files are streamed in over `zINSTREAM` and only a verdict comes back. Nothing is written to a volume. Only the signature database is downloaded, by `freshclam` running inside the container.
- `core/scan.py` `ClamdScanner` is a pure asyncio client (no new dependency). `VIRUS_SCANNER=clamd` selects it, with `CLAMD_HOST`, `CLAMD_PORT` (default 3310) and `CLAMD_TIMEOUT_S` (default 60) from the cell config. `recorded` stays for tests and local runs.
- Fail closed: `stream: OK` is clean, `stream: <sig> FOUND` is infected; anything else (unreachable, timeout, `INSTREAM size limit exceeded`, an unknown reply) raises `ScanUnavailable`. A caller must never accept a file on `ScanUnavailable` (failure mode F3).
- Size limits match the largest upload, 500 MB (NFR C1): `StreamMaxLength`, `MaxScanSize` and `MaxFileSize` are 500M and `AlertExceedsMax yes`, so a larger file is blocked rather than passed unscanned.
- Readiness: `ClamdScanner.ping()` sends `zPING` and needs `PONG`. The worker has no ready hook yet, so nothing calls it at start-up; the API does not need clamd.

**Consequences.**
- One more service to run (memory about 1.5 GB for the signature database). If it is down, uploads wait at "checking" and are not accepted.
- The image tag is pinned in `railway/clamav.Dockerfile`; pin its digest in the Railway build when the first image is chosen.
- Worker and API settings: `VIRUS_SCANNER=clamd`, `CLAMD_HOST=clamav.railway.internal`.

**Tests.** `api/tests/unit/test_foundation_clamd_scanner.py` (fake clamd), `api/tests/unit/test_foundation_clamd_live.py` (`@pytest.mark.clamd`, a clamav service container in `ci-api.yml`).
