# 0014 T-02 template suggestions run on request, not as a job

Date: 2026-10-17 · Status: accepted

**Problem.** The API rule is that long work returns 202 and runs in the worker. T-02 (suggest a report section for each heading of the expert's own Word template, screen 02) is an AI call, so a review asked whether it should be a job.

**Decision.**
- `POST /v1/me/template/suggestions` runs T-02 inside the request through the AI kit (identifier strip, cell allow-list, cost guard and schema check all apply as for any call).
- It reads only the template's headings, never case or patient data, and its answer is never saved: the expert decides each heading and saves the map with `PATCH /v1/me/template/mapping`.
- It does not wait out a busy model service (no retries): it answers 503 `processing_paused` at once, and the expert can try again or map the headings by hand.

**Consequences.**
- No job row, progress events or polling for S02; the expert waits a few seconds on one screen.
- If the nightly eval or live timings show T-02 over the request time budget, move it to a 202 job owned by slice C.
