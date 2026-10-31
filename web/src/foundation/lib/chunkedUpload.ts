// Sends a file to FastAPI in parts and proves it arrived as it left (architecture 5.3). Files go only to the API (rule 4).
//
// - Each part carries its own SHA-256 (`x-chunk-sha256`), worked out from the very bytes sent; a retry reads
//   the same slice again and works the hash out again.
// - The whole file's SHA-256 is worked out in a separate streaming pass (in a worker where the browser has
//   one), one part at a time, so a 500 MB file is never held in memory. It is sent when the upload starts and
//   again when it finishes, and the server compares it with what it stored.
// - Finishing hands the file to a background job: `complete` answers 202 and the upload is polled until the
//   server says `complete`, `open` (a part is missing: send it and finish again), `failed` or stopped.
// - A part is sent again with exponential backoff and full jitter, for as long as the fault may pass.
// - Every operation has its own Idempotency-Key, made once and reused on each retry of that operation.
import { ApiError, api, unwrap } from "../platform-client";
import { abortError, bytesOf, chunkRanges, sha256Stream } from "./hashCore";
import type { HashReply, HashRequest } from "./hash.worker";

export { chunkRanges };

/** The part size the API cuts files into; the file-hash pass reads in slices of the same size. */
export const HASH_SLICE = 8 * 1024 * 1024;
/** How many parts are on their way at once. */
export const CHUNK_CONCURRENCY = 3;
const BACKOFF_BASE_MS = 500;
const BACKOFF_CAP_MS = 30_000;
/** A part is given up on once it has failed for this long. */
const GIVE_UP_MS = 5 * 60_000;
const POLL_FIRST_MS = 2000;
const POLL_CAP_MS = 10_000;
/** How many times the server may reopen an upload for a missing part before we stop. */
const REOPENS = 3;

/** Time and chance, in one place so tests can take the waiting out. */
export const timing = {
  sleep: (ms: number, signal?: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      if (signal?.aborted) return reject(abortError());
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", stop);
        resolve();
      }, ms);
      const stop = () => {
        clearTimeout(timer);
        reject(abortError());
      };
      signal?.addEventListener("abort", stop, { once: true });
    }),
  random: () => Math.random(),
  now: () => Date.now(),
};

/** The longest a wait can be after the `attempt`th failure (1 is the first): 0.5 s doubling to 30 s. */
export const backoffCeiling = (attempt: number) => Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** (attempt - 1));
/** The wait before the poll after `looks` earlier ones: 2 s, growing by half each time to 10 s. */
export const pollDelay = (looks: number) => Math.min(POLL_CAP_MS, Math.round(POLL_FIRST_MS * 1.5 ** looks));

export type UploadPhase = "hashing" | "uploading" | "retrying" | "resuming" | "finalizing" | "done" | "failed";
export interface UploadProgress {
  phase: UploadPhase;
  /** 0 to 1. Only `done` is 1: it is never full before the server says the file is saved. */
  fraction: number;
}
export interface UploadOptions {
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
}

/** SHA-256 of the file, read in slices in order; off the main thread where a worker is available. */
export async function hashFile(file: Blob, chunkSize = HASH_SLICE, signal?: AbortSignal, onBytes?: (done: number) => void): Promise<string> {
  if (typeof Worker === "undefined") return sha256Stream(file, chunkSize, signal, onBytes);
  if (signal?.aborted) throw abortError();
  const worker = new Worker(new URL("./hash.worker.ts", import.meta.url), { type: "module" });
  return new Promise<string>((resolve, reject) => {
    const end = () => {
      signal?.removeEventListener("abort", stop);
      worker.terminate();
    };
    const stop = () => {
      end();
      reject(abortError());
    };
    signal?.addEventListener("abort", stop, { once: true });
    worker.onmessage = (event: MessageEvent<HashReply>) => {
      const reply = event.data;
      if ("progress" in reply) return onBytes?.(reply.progress);
      end();
      if ("hash" in reply) resolve(reply.hash);
      else reject(new ApiError(0, "hash_failed"));
    };
    worker.onerror = () => {
      end();
      reject(new ApiError(0, "hash_failed"));
    };
    worker.postMessage({ file, chunkSize } satisfies HashRequest);
  });
}

const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

/** A dropped connection, a timeout, being told to slow down, a 5xx, a damaged part or a busy upload: all may pass. */
function mayPass(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  return err.status === 0 || err.status === 408 || err.status === 429 || err.status >= 500 || err.code === "chunk_corrupted" || err.code === "upload_busy";
}

/** Runs `send` again after each wait, for up to five minutes of failing; any other fault ends it at once. */
async function withRetries<D>(send: () => Promise<D>, signal?: AbortSignal, onWait?: () => void): Promise<D> {
  let firstFailure: number | null = null;
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await send();
    } catch (err) {
      if (signal?.aborted) throw abortError();
      firstFailure ??= timing.now();
      if (!mayPass(err) || timing.now() - firstFailure >= GIVE_UP_MS) throw err;
      onWait?.();
      await timing.sleep(timing.random() * backoffCeiling(attempt), signal);
    }
  }
}

const key = () => crypto.randomUUID();
const withKey = (k: string) => ({ "Idempotency-Key": k });

/** What the poll can say; the fields are as in the API's Upload. */
interface UploadView {
  status: string;
  received?: number[];
  missing?: number[];
  document_id?: string | null;
  failure?: string | null;
}

/** Reasons the server may give for a failed upload that the screens have their own words for. */
// These are the only values the database allows in `uploads.failure` (migration 0124).
const KNOWN_FAILURES = new Set(["sha256_mismatch", "size_mismatch", "chunk_unreadable"]);

/** An upload the server has given up on, as an error with a code the screens have words for. */
function failedWith(code: string): ApiError {
  return new ApiError(409, code);
}

/** Uploads `file` as `kind`; `append` marks it as later records added to a case that already has a bundle. */
export async function uploadInParts(caseId: string, file: File, kind: string, append = false, options: UploadOptions = {}): Promise<string> {
  const { signal, onProgress } = options;
  // One pending record for this upload: each key is made before its request goes out and kept for its retries.
  const pending = { startKey: key(), completeKey: key(), cancelKey: key(), uploadId: null as string | null, closed: false };
  // After the server reopens an upload the bar holds where it was instead of going back.
  let floor = 0;
  let last = 0;
  const report = (phase: UploadPhase, fraction: number) => {
    last = phase === "done" ? 1 : Math.min(Math.max(fraction, phase === "failed" ? 0 : floor), 0.99);
    onProgress?.({ phase, fraction: last });
  };
  const gone = () => {
    if (signal?.aborted) throw abortError();
  };

  const cancel = async () => {
    if (!pending.uploadId || pending.closed) return;
    // Best effort, twice with the same key: the nightly sweep clears whatever is left.
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        await unwrap(api.POST("/v1/uploads/{upload_id}/cancel", { params: { path: { upload_id: pending.uploadId! } }, headers: withKey(pending.cancelKey) }));
        return;
      } catch (err) {
        if (!mayPass(err)) return;
        if (attempt < 2) await timing.sleep(timing.random() * backoffCeiling(attempt)).catch(() => undefined);
      }
    }
  };

  try {
    report("hashing", 0);
    const sha256 = await hashFile(file, HASH_SLICE, signal, (done) => report("hashing", file.size ? done / file.size : 0));
    gone();
    const started = await withRetries(
      () =>
        unwrap(
          api.POST("/v1/cases/{case_id}/uploads", {
            params: { path: { case_id: caseId } },
            headers: withKey(pending.startKey),
            body: { filename: file.name, kind, sha256, size: file.size, ...(append ? { append: true } : {}) },
          }),
        ),
      signal,
    );
    pending.uploadId = started.upload_id;
    const ranges = chunkRanges(file.size, started.chunk_size);
    // A part count the file cannot have is the server's mistake: stop with a real code (and cancel).
    if (started.chunk_count > ranges.length) throw failedWith("upload_failed");
    const total = Math.max(file.size, 1);
    const sent = new Set<number>();
    const bytesSent = () => [...sent].reduce((sum, n) => sum + (ranges[n - 1]![1] - ranges[n - 1]![0]), 0);

    const sendParts = async (numbers: number[]) => {
      gone();
      const queue = [...numbers];
      const stop = new AbortController();
      if (signal?.aborted) stop.abort();
      const stopAll = () => stop.abort();
      signal?.addEventListener("abort", stopAll, { once: true });
      const lane = async () => {
        for (let n = queue.shift(); n !== undefined; n = queue.shift()) {
          if (stop.signal.aborted) throw abortError();
          const [start, end] = ranges[n - 1]!;
          await withRetries(
            async () => {
              const part = await bytesOf(file.slice(start, end));
              const partSha = hex(await crypto.subtle.digest("SHA-256", part));
              return unwrap(
                api.PUT("/v1/uploads/{upload_id}/chunks/{n}", {
                  params: { path: { upload_id: started.upload_id, n } },
                  body: part as never,
                  bodySerializer: (b: unknown) => b as BodyInit,
                  headers: { "Content-Type": "application/octet-stream", "X-Chunk-SHA256": partSha },
                  signal: stop.signal,
                }),
              );
            },
            stop.signal,
            () => report("retrying", bytesSent() / total),
          );
          sent.add(n);
          report("uploading", bytesSent() / total);
        }
      };
      try {
        await Promise.all(Array.from({ length: CHUNK_CONCURRENCY }, () => lane().catch((e) => (stop.abort(), Promise.reject(e)))));
      } finally {
        signal?.removeEventListener("abort", stopAll);
      }
    };

    report("uploading", 0);
    const all = Array.from({ length: started.chunk_count }, (_, i) => i + 1);
    await sendParts(all);
    all.forEach((n) => sent.add(n));

    for (let round = 0; ; round += 1) {
      gone();
      report("finalizing", 0.99);
      gone(); // a cancel made while that was reported must stop here, before the finish is asked for
      // Each time we ask to finish is its own operation, so it has its own key; retries of it share one.
      const completeKey = round === 0 ? pending.completeKey : key();
      const answer: { document_id?: string | null } = await withRetries(
        () => unwrap(api.POST("/v1/uploads/{upload_id}/complete", { params: { path: { upload_id: started.upload_id } }, headers: withKey(completeKey), body: { sha256 } })),
        signal,
      );
      if (answer.document_id) return finished(answer.document_id);
      // The finish was accepted: the server cannot cancel it now, so a failure from here sends no cancel.
      pending.closed = true;
      const reopened = await pollUntilSettled(started.upload_id, started.chunk_count);
      if (typeof reopened === "string") return finished(reopened);
      if (round >= REOPENS) throw failedWith("upload_failed");
      pending.closed = false; // open again: a cancel is allowed
      floor = last;
      report("resuming", last);
      const missing = reopened;
      missing.forEach((n) => sent.delete(n));
      await sendParts(missing);
      missing.forEach((n) => sent.add(n));
    }
  } catch (err) {
    report("failed", 0);
    const closedByServer = pending.closed || (err instanceof ApiError && err.code === "upload_closed");
    if (!closedByServer) await cancel();
    throw err;
  }

  function finished(documentId: string): string {
    report("done", 1);
    return documentId;
  }

  /** Looks at the upload until it settles: the document's id, or (it was reopened) the numbers of the parts it lacks. */
  async function pollUntilSettled(uploadId: string, chunkCount: number): Promise<string | number[]> {
    let firstLook: number | null = null;
    for (let looks = 0; ; looks += 1) {
      await timing.sleep(pollDelay(looks), signal);
      firstLook ??= timing.now();
      const up: UploadView = await withRetries(() => unwrap(api.GET("/v1/uploads/{upload_id}", { params: { path: { upload_id: uploadId } } })), signal);
      if (up.status === "complete") {
        if (up.document_id) return up.document_id;
        throw failedWith("upload_failed"); // complete, yet no document to hand back
      }
      if (up.status === "open") {
        // Only a reopen if the server names parts it lacks. Open with none missing is the moment before the
        // row flips to finalizing, or an upload past its time that the nightly sweep has not closed yet.
        const lacks = Array.isArray(up.missing)
          ? up.missing
          : Array.isArray(up.received)
            ? Array.from({ length: chunkCount }, (_, i) => i + 1).filter((n) => !up.received!.includes(n))
            : []; // received left out: unknown, not none
        const missing = lacks.filter((n) => Number.isInteger(n) && n >= 1 && n <= chunkCount);
        if (missing.length > 0) return missing;
      } else if (up.status === "failed") {
        pending.closed = true;
        throw failedWith(up.failure && KNOWN_FAILURES.has(up.failure) ? up.failure : "upload_failed");
      } else if (up.status === "cancelled") {
        pending.closed = true;
        throw failedWith("upload_closed");
      } else if (up.status === "expired") {
        pending.closed = true;
        throw failedWith("upload_expired");
      }
      // finalizing, open with nothing missing, or anything new: keep looking, but not for ever.
      if (timing.now() - firstLook >= GIVE_UP_MS) throw failedWith("upload_failed");
    }
  }
}
