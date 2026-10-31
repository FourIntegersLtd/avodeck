// Sends a file to FastAPI in parts and proves it arrived as it left (architecture 5.3). Files go only to the API (rule 4).
//
// - Each part carries its own SHA-256 (`x-chunk-sha256`), worked out from the very bytes sent; a retry reads
//   the same slice again and works the hash out again.
// - The whole file's SHA-256 is worked out in a separate streaming pass, one part at a time, so a 500 MB file is
//   never held in memory. It is sent when the upload starts and again when it finishes, and the server compares
//   it with what it stored. A part sent under the wrong number is caught here, not by the per-part hashes.
import { createSHA256 } from "hash-wasm";
import { ApiError, api, unwrap } from "../platform-client";

export const PART_RETRIES = 3;
const RETRY_PAUSE_MS = 150;
/** The part size the API cuts files into; the file-hash pass reads in slices of the same size. */
export const HASH_SLICE = 8 * 1024 * 1024;

const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

/** The [start, end) byte offsets of each part of a file of `size` bytes cut every `chunkSize` bytes. */
export function chunkRanges(size: number, chunkSize: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (let start = 0; start < size; start += chunkSize) out.push([start, Math.min(start + chunkSize, size)]);
  return out;
}

async function bytesOf(file: Blob): Promise<Uint8Array> {
  if (typeof file.arrayBuffer === "function") return new Uint8Array(await file.arrayBuffer());
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(file);
  });
}

/** SHA-256 of the file, read in slices in order; only one slice is in memory at a time. */
export async function hashFile(file: Blob, chunkSize = HASH_SLICE): Promise<string> {
  const hasher = await createSHA256();
  hasher.init();
  for (const [start, end] of chunkRanges(file.size, chunkSize)) hasher.update(await bytesOf(file.slice(start, end)));
  return hasher.digest("hex");
}

/** Sends one part again after a short, growing wait, for a dropped connection, a 5xx or a part damaged on the way. */
async function withRetries<D>(send: () => Promise<D>): Promise<D> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await send();
    } catch (err) {
      const again = err instanceof ApiError && (err.status === 0 || err.status >= 500 || err.code === "chunk_corrupted");
      if (!again || attempt > PART_RETRIES) throw err;
      await new Promise((resolve) => setTimeout(resolve, RETRY_PAUSE_MS * attempt));
    }
  }
}

/** Uploads `file` as `kind`; `append` marks it as later records added to a case that already has a bundle. */
export async function uploadInParts(caseId: string, file: File, kind: string, append = false): Promise<string> {
  const sha256 = await hashFile(file);
  const started = await unwrap(
    api.POST("/v1/cases/{case_id}/uploads", {
      params: { path: { case_id: caseId } },
      body: { filename: file.name, kind, sha256, size: file.size, ...(append ? { append: true } : {}) },
    }),
  );
  const ranges = chunkRanges(file.size, started.chunk_size);
  for (let n = 1; n <= started.chunk_count; n += 1) {
    const [start, end] = ranges[n - 1]!;
    await withRetries(async () => {
      const part = await bytesOf(file.slice(start, end));
      const partSha = hex(await crypto.subtle.digest("SHA-256", part));
      return unwrap(
        api.PUT("/v1/uploads/{upload_id}/chunks/{n}", {
          params: { path: { upload_id: started.upload_id, n } },
          body: part as never,
          bodySerializer: (b: unknown) => b as BodyInit,
          headers: { "Content-Type": "application/octet-stream", "X-Chunk-SHA256": partSha },
        }),
      );
    });
  }
  const done = await unwrap(
    api.POST("/v1/uploads/{upload_id}/complete", { params: { path: { upload_id: started.upload_id } }, body: { sha256 } }),
  );
  return String(done.document_id);
}
