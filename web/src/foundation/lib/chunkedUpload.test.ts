// A file goes up in parts, each with its own SHA-256, and the whole file's SHA-256 (read in a separate
// pass, never all in memory) is sent when the upload finishes, so the server can prove what it stored.
import { createHash } from "node:crypto";
import { afterEach, beforeEach, expect, test } from "vitest";
import { ApiError } from "../platform-client";
import { http, HttpResponse, json, server } from "../../test/api";
import {
  backoffCeiling,
  CHUNK_CONCURRENCY,
  chunkRanges,
  hashFile,
  pollDelay,
  timing,
  uploadInParts,
  type UploadProgress,
} from "./chunkedUpload";

const CASE = "a1111111-1111-4111-8111-111111111111";
const UP = "a6666666-6666-4666-8666-666666666666";
const DOC = "b7777777-7777-4777-8777-777777777777";
const CHUNK = 7;

// No real waiting: every pause is recorded and moves a fake clock on.
const pauses: number[] = [];
let clock = 0;
const real = { ...timing };
beforeEach(() => {
  pauses.length = 0;
  clock = 0;
  timing.sleep = async (ms, signal) => {
    if (signal?.aborted) throw new DOMException("stopped", "AbortError");
    pauses.push(ms);
    clock += ms;
  };
  timing.now = () => clock;
  timing.random = () => 1;
});
afterEach(() => Object.assign(timing, real));

/** 20 bytes: not a multiple of the 7-byte part size, so the last part is short. */
const bytes = Uint8Array.from({ length: 20 }, (_, i) => (i * 37 + 11) % 256);
const file = () => new File([bytes], "records.pdf", { type: "application/pdf" });
const sha = (data: Uint8Array) => createHash("sha256").update(data).digest("hex");

function api_(put: (n: number, body: Uint8Array, sha: string | null) => Response, onComplete?: (body: unknown) => Response) {
  const seen: { started?: unknown; complete?: unknown } = {};
  server.use(
    http.post("/v1/cases/:caseId/uploads", async ({ request }) => {
      seen.started = await request.json();
      return json({ upload_id: UP, chunk_size: CHUNK, chunk_count: 3 }, 201);
    }),
    http.put("/v1/uploads/:id/chunks/:n", async ({ request, params }) =>
      put(Number(params.n), new Uint8Array(await request.arrayBuffer()), request.headers.get("x-chunk-sha256")),
    ),
    http.post("/v1/uploads/:id/cancel", () => json({ status: "cancelled" })),
    http.post("/v1/uploads/:id/complete", async ({ request }) => {
      seen.complete = await request.json();
      return onComplete ? onComplete(seen.complete) : json({ document_id: DOC }, 201);
    }),
  );
  return seen;
}

test("the parts are cut at the right offsets and the last one is short", () => {
  expect(chunkRanges(20, CHUNK)).toEqual([
    [0, 7],
    [7, 14],
    [14, 20],
  ]);
  expect(chunkRanges(14, CHUNK)).toEqual([
    [0, 7],
    [7, 14],
  ]);
});

test("the whole-file hash read in parts equals sha256sum of the file", async () => {
  expect(await hashFile(file(), CHUNK)).toBe(sha(bytes));
});

test("every part carries the hash of exactly the bytes sent, and the finish carries the whole-file hash", async () => {
  const got: Array<{ n: number; body: Uint8Array; sha: string | null }> = [];
  const seen = api_((n, body, header) => (got.push({ n, body, sha: header }), new HttpResponse(null, { status: 204 })));
  await expect(uploadInParts(CASE, file(), "records")).resolves.toBe(DOC);
  expect(got.map((g) => g.n).sort()).toEqual([1, 2, 3]); // three parts in flight at once: any order
  for (const g of got) {
    expect(g.sha).toBe(sha(g.body));
    expect(g.body).toEqual(bytes.slice((g.n - 1) * CHUNK, g.n * CHUNK));
  }
  expect(seen.complete).toEqual({ sha256: sha(bytes) });
  expect(seen.started).toMatchObject({ sha256: sha(bytes), size: 20 });
});

test("a part the server says was damaged is sent again from the same bytes, with its hash worked out again", async () => {
  const sent: Array<string | null> = [];
  api_((n, _body, header) => {
    if (n === 2) {
      sent.push(header);
      if (sent.length < 3) return HttpResponse.json({ error: { code: "chunk_corrupted" } }, { status: 422 });
    }
    return new HttpResponse(null, { status: 204 });
  });
  await uploadInParts(CASE, file(), "records");
  expect(sent).toEqual(Array(3).fill(sha(bytes.slice(7, 14))));
});

test("a file that does not verify is reported, not retried", async () => {
  let finishes = 0;
  api_(
    () => new HttpResponse(null, { status: 204 }),
    () => (++finishes, HttpResponse.json({ error: { code: "file_hash_mismatch" } }, { status: 422 })),
  );
  const err = await uploadInParts(CASE, file(), "records").catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err).toMatchObject({ code: "file_hash_mismatch" });
  expect(finishes).toBe(1);
});

// ---- retries and backoff -------------------------------------------------------------------------

const corrupted = () => HttpResponse.json({ error: { code: "chunk_corrupted" } }, { status: 422 });
const fail = (status: number, code: string) => HttpResponse.json({ error: { code } }, { status });
const ok204 = () => new HttpResponse(null, { status: 204 });

test("backoff doubles from half a second and is capped at thirty", () => {
  expect([1, 2, 3, 4, 5, 6, 7, 8].map(backoffCeiling)).toEqual([500, 1000, 2000, 4000, 8000, 16000, 30000, 30000]);
});

test("each wait is a random share of the ceiling (full jitter)", async () => {
  timing.random = () => 0.5;
  let tries = 0;
  api_((n) => (n === 1 && ++tries < 4 ? corrupted() : ok204()));
  await uploadInParts(CASE, file(), "records");
  expect(pauses).toEqual([250, 500, 1000]);
});

test("a part that stays damaged is given up on after five minutes of trying, not after a count", async () => {
  let tries = 0;
  api_((n) => (n === 1 ? (++tries, corrupted()) : ok204()));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "chunk_corrupted" });
  expect(clock).toBeGreaterThanOrEqual(5 * 60_000);
  expect(clock).toBeLessThan(5 * 60_000 + 30_000);
  expect(tries).toBeGreaterThan(10);
});

test.each([
  [0, "network_error"],
  [408, "request_timeout"],
  [429, "rate_limited"],
  [503, "unavailable"],
  [409, "upload_busy"],
])("a %i (%s) on a part is retried", async (status, code) => {
  let tries = 0;
  api_((n) => (n === 2 && ++tries === 1 ? (status === 0 ? HttpResponse.error() : fail(status, code)) : ok204()));
  await expect(uploadInParts(CASE, file(), "records")).resolves.toBe(DOC);
  expect(tries).toBe(2);
});

test.each([400, 403, 404, 413, 422])("a plain %i on a part is not retried", async (status) => {
  let tries = 0;
  api_((n) => (n === 1 ? (++tries, fail(status, "chunk_size_wrong")) : ok204()));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ status });
  expect(tries).toBe(1);
  expect(pauses).toEqual([]);
});

test("upload_closed ends the whole upload at once", async () => {
  let puts = 0;
  api_(() => (++puts, fail(409, "upload_closed")));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "upload_closed" });
  expect(puts).toBeLessThanOrEqual(CHUNK_CONCURRENCY);
});

test("no more than three parts are in flight at once", async () => {
  expect(CHUNK_CONCURRENCY).toBe(3);
  server.use(http.post("/v1/cases/:caseId/uploads", () => json({ upload_id: UP, chunk_size: 1, chunk_count: 20 }, 201)));
  let live = 0;
  let peak = 0;
  server.use(
    http.post("/v1/uploads/:id/complete", () => json({ document_id: DOC }, 201)),
    http.put("/v1/uploads/:id/chunks/:n", async () => {
      peak = Math.max(peak, ++live);
      await new Promise((r) => setTimeout(r, 5));
      live -= 1;
      return ok204();
    }),
  );
  await uploadInParts(CASE, file(), "records");
  expect(peak).toBe(3);
});

// ---- keys ------------------------------------------------------------------------------------------

test("the start and complete keys are made once and reused on every retry", async () => {
  const starts: Array<string | null> = [];
  const completes: Array<string | null> = [];
  let startTries = 0;
  let completeTries = 0;
  server.use(
    http.post("/v1/cases/:caseId/uploads", ({ request }) => {
      starts.push(request.headers.get("idempotency-key"));
      return ++startTries === 1 ? fail(503, "unavailable") : json({ upload_id: UP, chunk_size: CHUNK, chunk_count: 3 }, 201);
    }),
    http.put("/v1/uploads/:id/chunks/:n", ok204),
    http.post("/v1/uploads/:id/complete", ({ request }) => {
      completes.push(request.headers.get("idempotency-key"));
      return ++completeTries < 3 ? fail(409, "upload_busy") : json({ document_id: DOC }, 201);
    }),
  );
  await uploadInParts(CASE, file(), "records");
  expect(starts).toHaveLength(2);
  expect(starts[0]).toBeTruthy();
  expect(starts[1]).toBe(starts[0]);
  expect(completes).toHaveLength(3);
  expect(completes[0]).toBeTruthy();
  expect(new Set(completes).size).toBe(1);
  expect(completes[0]).not.toBe(starts[0]);
});

// ---- the 202 and the poll --------------------------------------------------------------------------

type Poll = Record<string, unknown>;
const view = (over: Poll): Poll => ({ upload_id: UP, case_id: CASE, size: 20, chunk_size: CHUNK, chunk_count: 3, received: [1, 2, 3], missing: [], document_id: null, ...over });

/** The parts, a 202 from complete, then the polls in order (the last one repeats). */
function polled(polls: Poll[], put: (n: number) => Response = ok204) {
  const calls = { puts: [] as number[], completes: [] as Array<string | null>, polls: 0, cancels: [] as Array<string | null> };
  server.use(
    http.post("/v1/cases/:caseId/uploads", () => json({ upload_id: UP, chunk_size: CHUNK, chunk_count: 3 }, 201)),
    http.put("/v1/uploads/:id/chunks/:n", ({ params }) => (calls.puts.push(Number(params.n)), put(Number(params.n)))),
    http.post("/v1/uploads/:id/complete", ({ request }) => (calls.completes.push(request.headers.get("idempotency-key")), json({ status: "finalizing" }, 202))),
    http.get("/v1/uploads/:id", () => json(polls[Math.min(calls.polls++, polls.length - 1)]!)),
    http.post("/v1/uploads/:id/cancel", ({ request }) => (calls.cancels.push(request.headers.get("idempotency-key")), json(view({ status: "cancelled" })))),
  );
  return calls;
}

test("poll waits two seconds, backing off to ten", () => {
  expect([0, 1, 2, 3, 4, 5, 6].map(pollDelay)).toEqual([2000, 3000, 4500, 6750, 10000, 10000, 10000]);
});

test("complete answers 202, then the upload is polled until it says complete", async () => {
  const calls = polled([view({ status: "finalizing" }), view({ status: "finalizing" }), view({ status: "complete", document_id: DOC })]);
  await expect(uploadInParts(CASE, file(), "records")).resolves.toBe(DOC);
  expect(calls.polls).toBe(3);
  expect(pauses.slice(-3)).toEqual([2000, 3000, 4500].slice(0, 3));
  expect(calls.cancels).toEqual([]);
});

test("progress never reaches 100% before the server says complete", async () => {
  polled([view({ status: "finalizing" }), view({ status: "complete", document_id: DOC })]);
  const seen: UploadProgress[] = [];
  await uploadInParts(CASE, file(), "records", false, { onProgress: (p) => seen.push({ ...p }) });
  const done = seen.findIndex((p) => p.phase === "done");
  expect(done).toBe(seen.length - 1);
  expect(seen.slice(0, done).every((p) => p.fraction < 1)).toBe(true);
  expect(seen[done]!.fraction).toBe(1);
  const phases = seen.map((p) => p.phase);
  expect(phases[0]).toBe("hashing");
  expect(phases).toContain("uploading");
  expect(phases).toContain("finalizing");
});

test("a part waiting to be sent again is reported as retrying", async () => {
  let tries = 0;
  polled([view({ status: "complete", document_id: DOC })], (n) => (n === 1 && ++tries === 1 ? fail(503, "unavailable") : ok204()));
  const seen: string[] = [];
  await uploadInParts(CASE, file(), "records", false, { onProgress: (p) => seen.push(p.phase) });
  expect(seen).toContain("retrying");
});

test("a poll that says open resends only the missing parts, then completes again with a new key", async () => {
  const calls = polled([view({ status: "open", received: [1, 3], missing: [2] }), view({ status: "finalizing" }), view({ status: "complete", document_id: DOC })]);
  const seen: string[] = [];
  await expect(uploadInParts(CASE, file(), "records", false, { onProgress: (p) => seen.push(p.phase) })).resolves.toBe(DOC);
  expect(calls.puts.slice(0, 3).sort()).toEqual([1, 2, 3]); // three parts in flight at once: any order
  expect(calls.puts.slice(3)).toEqual([2]);
  expect(calls.completes).toHaveLength(2);
  expect(calls.completes[1]).not.toBe(calls.completes[0]);
  expect(seen).toContain("resuming");
});

test("a failed upload shows the reason the server gave and does not send a cancel", async () => {
  const calls = polled([view({ status: "failed", failure: "sha256_mismatch" })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "sha256_mismatch" });
  expect(calls.cancels).toEqual([]);
});

test("a failed upload with a code we do not know is still an ApiError with a plain fallback", async () => {
  polled([view({ status: "failed", failure: "something_new" })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "upload_failed" });
});

test.each(["cancelled", "expired"])("a %s upload stops without a cancel", async (status) => {
  const calls = polled([view({ status })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: status === "expired" ? "upload_expired" : "upload_closed" });
  expect(calls.cancels).toEqual([]);
});

// ---- cancel ----------------------------------------------------------------------------------------

test("abandoning an upload sends a cancel once, with its own key, and stops", async () => {
  const calls = polled([view({ status: "finalizing" })]);
  const abort = new AbortController();
  const progress = (p: UploadProgress) => {
    if (p.phase === "finalizing") abort.abort();
  };
  await expect(uploadInParts(CASE, file(), "records", false, { signal: abort.signal, onProgress: progress })).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.cancels).toHaveLength(1);
  expect(calls.cancels[0]).toBeTruthy();
  expect(calls.cancels[0]).not.toBe(calls.completes[0]);
});

test("a part that fails for good cancels the upload", async () => {
  const calls = polled([view({ status: "complete", document_id: DOC })], (n) => (n === 2 ? fail(422, "chunk_size_wrong") : ok204()));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "chunk_size_wrong" });
  expect(calls.cancels).toHaveLength(1);
});

// ---- review fixes ----------------------------------------------------------------------------------

test("an upload still finalizing after five minutes gives up with a plain failure and no cancel", async () => {
  const calls = polled([view({ status: "finalizing" })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "upload_failed" });
  expect(clock).toBeGreaterThanOrEqual(5 * 60_000);
  expect(calls.cancels).toEqual([]);
});

test.each([{ document_id: null }, {}])("a complete upload with no document id is a failure, not a reason to keep polling (%j)", async (extra) => {
  const calls = polled([view({ status: "complete", ...extra })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "upload_failed" });
  expect(calls.polls).toBe(1);
});

test("open with nothing missing is not a reopen: keep polling, resend nothing, never complete twice", async () => {
  const calls = polled([view({ status: "open", received: [1, 2, 3], missing: [] }), view({ status: "open", received: [1, 2, 3], missing: [] }), view({ status: "complete", document_id: DOC })]);
  await expect(uploadInParts(CASE, file(), "records")).resolves.toBe(DOC);
  expect(calls.puts).toHaveLength(3);
  expect(calls.completes).toHaveLength(1);
  expect(calls.cancels).toEqual([]);
});

test("a poll that leaves out received and missing is unknown, not empty: the whole file is not resent", async () => {
  const calls = polled([{ upload_id: UP, status: "open" }, view({ status: "complete", document_id: DOC })]);
  await expect(uploadInParts(CASE, file(), "records")).resolves.toBe(DOC);
  expect(calls.puts).toHaveLength(3);
  expect(calls.completes).toHaveLength(1);
});

test.each(["sha256_mismatch", "size_mismatch", "chunk_unreadable"])("the %s reason the database allows is passed on", async (failure) => {
  polled([view({ status: "failed", failure })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: failure });
});

test.each(["file_hash_mismatch", "chunks_missing", "file_sha_missing", "chunk_corrupted"])("%s can never be a stored reason, so it falls back to the plain one", async (failure) => {
  polled([view({ status: "failed", failure })]);
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "upload_failed" });
});

test("cancelling while the upload is being opened sends no parts, then cancels", async () => {
  const calls = polled([view({ status: "complete", document_id: DOC })]);
  const abort = new AbortController();
  server.use(
    http.post("/v1/cases/:caseId/uploads", () => {
      abort.abort();
      return json({ upload_id: UP, chunk_size: CHUNK, chunk_count: 3 }, 201);
    }),
  );
  await expect(uploadInParts(CASE, file(), "records", false, { signal: abort.signal })).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.puts).toEqual([]);
  expect(calls.cancels).toHaveLength(1);
});

test("cancelling as missing parts are about to be resent sends none of them", async () => {
  const abort = new AbortController();
  const calls = polled([view({ status: "open", received: [1, 3], missing: [2] })]);
  await expect(
    uploadInParts(CASE, file(), "records", false, { signal: abort.signal, onProgress: (p) => p.phase === "resuming" && abort.abort() }),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.puts).toHaveLength(3);
});

test("cancelling after the finish was accepted sends no cancel, which the server would refuse", async () => {
  const calls = polled([view({ status: "finalizing" })]);
  const abort = new AbortController();
  server.use(
    http.post("/v1/uploads/:id/complete", () => {
      abort.abort();
      return json({ status: "finalizing" }, 202);
    }),
  );
  await expect(uploadInParts(CASE, file(), "records", false, { signal: abort.signal })).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.cancels).toEqual([]);
});

test("a part count larger than the file allows fails with a real code, not a TypeError, and cancels", async () => {
  const calls = polled([view({ status: "complete", document_id: DOC })]);
  server.use(http.post("/v1/cases/:caseId/uploads", () => json({ upload_id: UP, chunk_size: CHUNK, chunk_count: 5 }, 201)));
  const err = await uploadInParts(CASE, file(), "records").catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err).toMatchObject({ code: "upload_failed" });
  expect(calls.puts).toEqual([]);
  expect(calls.cancels).toHaveLength(1);
});

test("the bar does not go backwards when the server asks for parts again", async () => {
  polled([view({ status: "open", received: [1], missing: [2, 3] }), view({ status: "complete", document_id: DOC })]);
  const seen: UploadProgress[] = [];
  await uploadInParts(CASE, file(), "records", false, { onProgress: (p) => seen.push({ ...p }) });
  const from = seen.findIndex((p) => p.phase === "finalizing");
  const after = seen.slice(from).filter((p) => p.phase !== "done");
  for (let i = 1; i < after.length; i += 1) expect(after[i]!.fraction).toBeGreaterThanOrEqual(after[i - 1]!.fraction);
});

test("the last cancel attempt does not wait afterwards", async () => {
  server.use(http.post("/v1/uploads/:id/cancel", () => fail(503, "unavailable")));
  polled([view({ status: "complete", document_id: DOC })], (n) => (n === 2 ? fail(422, "chunk_size_wrong") : ok204()));
  server.use(http.post("/v1/uploads/:id/cancel", () => fail(503, "unavailable")));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "chunk_size_wrong" });
  expect(pauses).toHaveLength(1);
});
