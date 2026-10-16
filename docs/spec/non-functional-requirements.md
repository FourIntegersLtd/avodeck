# Non-functional requirements

These are the numbers the build is tested against. Each has an owner slice and a test or monitor. Targets marked **(spike)** are provisional until the spike results are in.

## Performance

| ID | Requirement | Target | Owner | How checked |
|---|---|---|---|---|
| P1 | Bundle processing, 1,000 pages, upload complete → "bundle ready" | ≤ 20 min p90 **(spike 5)** | A | Staging run on synthetic bundle; job timings |
| P2 | Bundle processing, 4,000 pages | ≤ 75 min p90 | A | Nightly synthetic run |
| P3 | Reader: next page image shown | ≤ 300 ms p95 (prefetched), ≤ 1 s cold | B | Playwright timing |
| P4 | Reader: suggestions appear after "Mark as read" | ≤ 500 ms p95 | B | API timing test |
| P5 | Lint warnings while writing | ≤ 2 s p95 | E | Timing test with recorded responses |
| P6 | Report build, 40-page report | ≤ 60 s | F | Job timing |
| P7 | Final check | ≤ 30 s | F | Job timing |
| P8 | Timeline with 1,500 events renders | ≤ 1 s | D | Playwright |
| P9 | Any API call (excluding streaming and jobs) | ≤ 400 ms p95 | All | Server metrics |
| P10 | Witness-box offline search | ≤ 300 ms, network off | I | Playwright offline |

## Capacity and limits

| ID | Limit | Value |
|---|---|---|
| C1 | Largest single upload | 500 MB, 4,000 pages |
| C2 | Largest case | 8,000 pages across all documents |
| C3 | Concurrent bundle jobs per cell at launch | 5 (queue beyond that, with an honest wait estimate) |
| C4 | Active cases per expert | 50 |
| C5 | Experts per cell before scaling review | 200 |

## Availability and recovery

| ID | Requirement | Target |
|---|---|---|
| A1 | Monthly availability of the web app and API (excluding announced maintenance) | 99.5% (about 3.6 hours a month) |
| A2 | Recovery point objective: maximum data lost in a disaster | 15 minutes (Supabase point-in-time recovery) |
| A3 | Recovery time objective: time to restore service | 8 hours |
| A4 | Restore drill | Before the first real case, then every 6 months; result logged in `docs/ops/` |
| A5 | No single job failure loses expert work | Page reads, verifications and opinion text are written synchronously, never only in a job |

## Cost

| ID | Requirement | Target |
|---|---|---|
| K1 | OCR plus AI cost per 1,000-page case | ≤ £12 **(spike 5)**; alert at £20 |
| K2 | Hosting at 10 experts | ≤ $65 a month (research doc) |
| K3 | Cost guard | A job pauses and alerts if one case passes 3× the expected AI cost |

## Accuracy (AI)

| ID | Requirement | Target |
|---|---|---|
| Q1 | T-07b extraction precision on the synthetic eval set | ≥ 0.95 (nightly, blocks prompt or model changes) |
| Q2 | T-07b recall | ≥ 0.90 |
| Q3 | Share of pages sent to screen 10 | ≤ baseline + 5 points |
| Q4 | T-06 questions exact substring of the letter | 100% (rejected otherwise) |
| Q5 | Planted duplicates found; near-duplicates (lab copies) not set aside | 100% / 0 false duplicates |

## Security and privacy

| ID | Requirement |
|---|---|
| S1 | All patient data stored and processed in the home cell region (rule 5) |
| S2 | MFA for every account; step-up for risky actions (rule 10) |
| S3 | No patient text in logs, traces, analytics or error reports (rule 6); automated log scan in CI and weekly in production |
| S4 | Per-case encryption keys; deletion destroys the key (ADR 0010) |
| S5 | Dependency vulnerabilities: critical fixed within 7 days, high within 30 |
| S6 | Penetration test before the first paying customer |

## Usability and accessibility

| ID | Requirement |
|---|---|
| U1 | WCAG 2.2 AA on every screen (axe in CI, plus a manual keyboard pass per slice) |
| U2 | Body text ≥ 17 px, buttons ≥ 52 px, one primary action per screen |
| U3 | Works on the last two versions of Chrome, Edge and Safari; laptop screens 1280 px and up; reader usable on a 10-inch tablet |
| U4 | A new expert completes profile and first case set-up in under 20 minutes without help (usability test with design partners) |

## Maintainability

| ID | Requirement |
|---|---|
| M1 | Coverage floor 85% on slice `service.py` and `router.py`; 100% on `core/` safety rules |
| M2 | A new pack (specialty, case type or jurisdiction) adds no slice code changes (rule 11); CI pack-schema validation |
| M3 | Every merge to `main` deployable; staging deploy on merge, production by manual promotion |
| M4 | Database migrations are backward-compatible (expand, migrate, contract over separate releases) |
