---
name: add-endpoint
description: Add or change a FastAPI endpoint in an Avodeck slice, with ownership, step-up, CSRF, schemas, tests and the regenerated frontend client.
---

# Add an endpoint

Write the API test first (see `tdd-feature`).

1. **Find it in the spec.** Locate the row in `docs/architecture.md` section 5.2. If it isn't there, ask before inventing one.
2. **Schemas** in `api/app/features/<feature>/schemas.py`:
   - Request and response Pydantic models with explicit types; no `dict` or `Any`.
   - Shared shapes (facts, questions, report versions) come from `app/core/contracts/`; do not redefine them.
   - Responses never include patient text the screen does not need.
3. **Route** in `api/app/features/<feature>/router.py`:
   - Case routes: `case = Depends(require_case_access("<action>"))`; add the action to `docs/spec/permissions.md` first, with a test for each role.
   - Edits: return an `ETag` and require `If-Match` (412 on stale). Creates, sends, signs and serves: honour `Idempotency-Key`.
   - State-changing: `Depends(require_csrf)`.
   - Sign, send, export, delete, retention: `Depends(require_step_up)` (marked 🔒 in section 5.2).
   - Long work: enqueue a job and return `202` with `{"job_id"}`.
   - Files: stream through `core/storage.py`; set `Cache-Control: private, no-store`.
   - Errors: raise `ApiError` with a standard code.
4. **Service and repo:** logic in `service.py`; SQL only in `repo.py`, only for your slice's tables.
5. **Country-neutral:** names and paths must not be UK-specific (`written-questions`, not `p35`). Rules, deadlines and wording come from the case's jurisdiction pack.
6. **Audit:** write an `audit_log` entry for sign, send, export, delete, retention, verification and reads of locked versions.
7. **Tests must cover:** success; 404 for another expert's case; 403 without CSRF; 403 `step_up_required` where 🔒; 409/423 states; no patient text in the error body.
8. **Regenerate the client:** `make api-client`. Commit the updated `openapi.json` and `web/src/foundation/platform-client/` with the feature.
9. **Contracts only grow.** Adding a field is fine; removing or renaming one used elsewhere is not.
