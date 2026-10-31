// An upload survives a refresh: a small record (never the file name, the contents or a token) is kept in
// localStorage while it runs, and on return the server is asked first, then the same file is asked for again.
import { createHash } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { timing, uploadInParts } from "./chunkedUpload";
import { findResumable, resumeUpload } from "./uploadResume";
import { listUploads, saveUpload, uploadKey, type StoredUpload } from "./uploadStore";

const PERSON = "p0000000-0000-4000-8000-000000000001";
const CASE = "a1111111-1111-4111-8111-111111111111";
const UP = "a6666666-6666-4666-8666-666666666666";
const DOC = "b7777777-7777-4777-8777-777777777777";
const CHUNK = 7;
const real = { ...timing };
beforeEach(() => {
  localStorage.clear();
  timing.sleep = async () => undefined;
  timing.random = () => 1;
});
afterEach(() => Object.assign(timing, real));

const bytes = Uint8Array.from({ length: 20 }, (_, i) => (i * 37 + 11) % 256);
const file = (name = "secret-name.pdf", data = bytes, lastModified = 1000) => new File([data], name, { type: "application/pdf", lastModified });
const sha = (d: Uint8Array) => createHash("sha256").update(d).digest("hex");
const ok = () => new Response(null, { status: 204 });
const opts = { personId: PERSON };
const view = (over: Record<string, unknown> = {}) => ({ upload_id: UP, status: "open", size: 20, chunk_size: CHUNK, chunk_count: 3, received: [1], missing: [2, 3], document_id: null, expires_at: new Date(Date.now() + 3600_000).toISOString(), ...over });
const record = (over: Partial<StoredUpload> = {}): StoredUpload => ({
  uploadId: UP,
  size: 20,
  lastModified: 1000,
  sha256: sha(bytes),
  keys: { startKey: "s", completeKey: "c", cancelKey: "x" },
  kind: "records",
  append: true,
  slot: "slot-1",
  expiresAt: new Date(Date.now() + 3600_000).toISOString(),
  ...over,
});

function serve(parts: number[] = [], got: { complete: number; cancel: number; keys: string[] } = { complete: 0, cancel: 0, keys: [] }, status = view()) {
  server.use(
    http.get("/v1/uploads/:id", () => {
      // After the finish was asked for, the upload settles as complete.
      return json(got.complete > 0 ? view({ status: "complete", document_id: DOC }) : status);
    }),
    http.put("/v1/uploads/:id/chunks/:n", ({ params }) => (parts.push(Number(params.n)), ok())),
    // The real contract: finishing answers 202 and the document is read from the polled upload.
    http.post("/v1/uploads/:id/complete", ({ request }) => (got.complete++, got.keys.push(request.headers.get("idempotency-key") ?? ""), json({ status: "finalizing" }, 202))),
    http.post("/v1/uploads/:id/cancel", () => (got.cancel++, json({ status: "cancelled" }))),
  );
  return got;
}

test("the record is written before the start request goes out and holds only ids, sizes, hash, keys and expiry", async () => {
  let atStart: Record<string, unknown>[] = [];
  server.use(
    http.post("/v1/cases/:caseId/uploads", () => {
      atStart = listUploads(PERSON, CASE) as never;
      return json(view({ status: "open" }), 201);
    }),
  );
  serve();
  await uploadInParts(CASE, file(), "records", false, opts);
  expect(atStart).toHaveLength(1);
  expect(Object.keys(atStart[0]!).sort()).toEqual(["append", "expiresAt", "keys", "kind", "lastModified", "sha256", "size", "slot", "uploadId"]);
  expect(JSON.stringify(atStart)).not.toContain("secret-name");
});

test("the record is under av.upload.<person>.<case>.<upload> once the upload has an id, and is gone when it completes", async () => {
  let during: string[] = [];
  server.use(http.post("/v1/cases/:caseId/uploads", () => json(view(), 201)));
  serve();
  server.use(
    http.put("/v1/uploads/:id/chunks/:n", () => {
      during = Object.keys(localStorage);
      return ok();
    }),
  );
  await uploadInParts(CASE, file(), "records", false, opts);
  expect(during).toContain(uploadKey(PERSON, CASE, UP));
  expect(during).toContain(`av.upload.${PERSON}.${CASE}.${UP}`);
  expect(listUploads(PERSON, CASE)).toEqual([]);
});

test("the record is removed when the upload fails and when it is cancelled", async () => {
  server.use(http.post("/v1/cases/:caseId/uploads", () => json(view(), 201)));
  serve();
  server.use(http.put("/v1/uploads/:id/chunks/:n", () => HttpResponse.json({ error: { code: "file_too_large" } }, { status: 422 })));
  await expect(uploadInParts(CASE, file(), "records", false, opts)).rejects.toBeTruthy();
  expect(listUploads(PERSON, CASE)).toEqual([]);
  const stop = new AbortController();
  server.use(
    http.put("/v1/uploads/:id/chunks/:n", () => {
      stop.abort();
      return ok();
    }),
  );
  await expect(uploadInParts(CASE, file(), "records", false, { ...opts, signal: stop.signal })).rejects.toBeTruthy();
  expect(listUploads(PERSON, CASE)).toEqual([]);
});

test("on return the server is asked first; an open upload asks for the file, then only the missing parts are sent", async () => {
  saveUpload(PERSON, CASE, record());
  const parts: number[] = [];
  const got = serve(parts);
  const found = await findResumable(PERSON, CASE);
  expect(found).toMatchObject({ state: "needs_file" });
  await expect(resumeUpload(CASE, found!, file("renamed.pdf"), opts)).resolves.toBe(DOC);
  expect(parts.sort()).toEqual([2, 3]);
  expect(got.complete).toBe(1);
  expect(got.keys[0]).not.toBe("c"); // a stored key would replay the earlier 202 and enqueue no new job
  expect(listUploads(PERSON, CASE)).toEqual([]);
});

test("a finalizing upload the server reopens asks for the file again and keeps the record", async () => {
  saveUpload(PERSON, CASE, record());
  let looks = 0;
  server.use(http.get("/v1/uploads/:id", () => json(++looks === 1 ? view({ status: "finalizing" }) : view({ status: "open", received: [1], missing: [2, 3] }))));
  const found = await findResumable(PERSON, CASE);
  await expect(resumeUpload(CASE, found!, null, opts)).rejects.toMatchObject({ code: "upload_needs_file" });
  expect(listUploads(PERSON, CASE)).toHaveLength(1);
});

test("a resumed upload keeps the kind and append it started with, and a start replay sends the same body", async () => {
  saveUpload(PERSON, CASE, record({ uploadId: null, kind: "instructions", append: false }));
  let body: Record<string, unknown> = {};
  server.use(http.post("/v1/cases/:caseId/uploads", async ({ request }) => ((body = (await request.json()) as Record<string, unknown>), json(view({ status: "open", received: [] }), 201))));
  serve();
  const found = await findResumable(PERSON, CASE);
  await resumeUpload(CASE, found!, file(), opts);
  expect(body).toMatchObject({ kind: "instructions" });
  expect(body.append).toBeUndefined();
});

test("only records of the kind the screen asks for are offered", async () => {
  saveUpload(PERSON, CASE, record({ kind: "instructions", append: false }));
  serve();
  expect(await findResumable(PERSON, CASE, { append: true })).toBeNull();
  saveUpload(PERSON, CASE, record({ kind: "instructions", append: false }));
  expect(await findResumable(PERSON, CASE, { append: false })).not.toBeNull();
});

test("two uploads started at once on one case keep separate records before the server answers", async () => {
  const seen: number[] = [];
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  server.use(
    http.post("/v1/cases/:caseId/uploads", async () => {
      seen.push(listUploads(PERSON, CASE).length);
      await gate;
      return json(view({ status: "open", upload_id: crypto.randomUUID() }), 201);
    }),
  );
  serve();
  server.use(http.put("/v1/uploads/:id/chunks/:n", () => new Promise<Response>(() => undefined)));
  const a = new AbortController();
  void uploadInParts(CASE, file("a.pdf"), "records", false, { ...opts, signal: a.signal }).catch(() => undefined);
  void uploadInParts(CASE, file("b.pdf", Uint8Array.from(bytes, (b) => b ^ 3)), "other", false, { ...opts, signal: a.signal }).catch(() => undefined);
  await vi.waitFor(() => expect(seen).toHaveLength(2));
  expect(Math.max(...seen)).toBe(2);
  release();
  await vi.waitFor(() => expect(listUploads(PERSON, CASE)).toHaveLength(2));
  a.abort();
});

test("a different file is refused with a clear code, nothing is sent and the record is kept", async () => {
  saveUpload(PERSON, CASE, record());
  const parts: number[] = [];
  serve(parts);
  const found = await findResumable(PERSON, CASE);
  const other = Uint8Array.from(bytes, (b) => b ^ 1);
  await expect(resumeUpload(CASE, found!, file("x.pdf", other), opts)).rejects.toMatchObject({ code: "upload_wrong_file" });
  await expect(resumeUpload(CASE, found!, file("x.pdf", bytes, 5), opts)).rejects.toMatchObject({ code: "upload_wrong_file" });
  expect(parts).toEqual([]);
  expect(listUploads(PERSON, CASE)).toHaveLength(1);
});

test("a same-size file with other contents is refused after the rehash", async () => {
  saveUpload(PERSON, CASE, record({ sha256: sha(Uint8Array.from(bytes, (b) => b ^ 1)) }));
  serve();
  const found = await findResumable(PERSON, CASE);
  await expect(resumeUpload(CASE, found!, file(), opts)).rejects.toMatchObject({ code: "upload_wrong_file" });
});

test("a finalizing upload is polled to its document without asking for the file", async () => {
  saveUpload(PERSON, CASE, record());
  let looks = 0;
  server.use(http.get("/v1/uploads/:id", () => json(++looks === 1 ? view({ status: "finalizing" }) : view({ status: "complete", document_id: DOC }))));
  const found = await findResumable(PERSON, CASE);
  expect(found).toMatchObject({ state: "finalizing" });
  await expect(resumeUpload(CASE, found!, null, opts)).resolves.toBe(DOC);
  expect(listUploads(PERSON, CASE)).toEqual([]);
});

test("a complete upload is finished with its document id", async () => {
  saveUpload(PERSON, CASE, record());
  serve([], undefined, view({ status: "complete", document_id: DOC }));
  expect(await findResumable(PERSON, CASE)).toMatchObject({ state: "complete", documentId: DOC });
});

test("any other status, a past expiry or an unknown upload drops the record", async () => {
  for (const status of ["failed", "cancelled", "expired"]) {
    saveUpload(PERSON, CASE, record());
    serve([], undefined, view({ status }));
    expect(await findResumable(PERSON, CASE)).toBeNull();
    expect(listUploads(PERSON, CASE)).toEqual([]);
  }
  saveUpload(PERSON, CASE, record({ expiresAt: new Date(Date.now() - 1000).toISOString() }));
  expect(await findResumable(PERSON, CASE)).toBeNull();
  expect(listUploads(PERSON, CASE)).toEqual([]);
  saveUpload(PERSON, CASE, record());
  server.use(http.get("/v1/uploads/:id", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })));
  expect(await findResumable(PERSON, CASE)).toBeNull();
  expect(listUploads(PERSON, CASE)).toEqual([]);
});

test("another person's or another case's record is not seen", async () => {
  saveUpload("someone-else", CASE, record());
  saveUpload(PERSON, "other-case", record());
  expect(await findResumable(PERSON, CASE)).toBeNull();
});
