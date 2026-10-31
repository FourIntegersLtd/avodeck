// A file goes up in parts, each with its own SHA-256, and the whole file's SHA-256 (read in a separate
// pass, never all in memory) is sent when the upload finishes, so the server can prove what it stored.
import { createHash } from "node:crypto";
import { expect, test } from "vitest";
import { ApiError } from "../platform-client";
import { http, HttpResponse, json, server } from "../../test/api";
import { chunkRanges, hashFile, uploadInParts } from "./chunkedUpload";

const CASE = "a1111111-1111-4111-8111-111111111111";
const UP = "a6666666-6666-4666-8666-666666666666";
const DOC = "b7777777-7777-4777-8777-777777777777";
const CHUNK = 7;

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
  expect(got.map((g) => g.n)).toEqual([1, 2, 3]);
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

test("a part that stays damaged fails after a few tries", async () => {
  let tries = 0;
  api_((n) => (n === 1 ? (++tries, HttpResponse.json({ error: { code: "chunk_corrupted" } }, { status: 422 })) : new HttpResponse(null, { status: 204 })));
  await expect(uploadInParts(CASE, file(), "records")).rejects.toMatchObject({ code: "chunk_corrupted" });
  expect(tries).toBe(4);
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
