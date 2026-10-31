// A file's SHA-256, read one part at a time so a 500 MB file is never held in memory. Pure JavaScript
// (no WebAssembly): the production Content-Security-Policy has no 'wasm-unsafe-eval', so a WebAssembly
// hasher cannot start in Chrome or Firefox. Runs in a worker (hash.worker.ts) or, with none, on the page.
import { sha256 } from "@noble/hashes/sha2.js";

const hex = (bytes: Uint8Array) => Array.from(bytes, (x) => x.toString(16).padStart(2, "0")).join("");

/** The [start, end) byte offsets of each part of a file of `size` bytes cut every `chunkSize` bytes. */
export function chunkRanges(size: number, chunkSize: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (let start = 0; start < size; start += chunkSize) out.push([start, Math.min(start + chunkSize, size)]);
  return out;
}

export async function bytesOf(file: Blob): Promise<Uint8Array> {
  if (typeof file.arrayBuffer === "function") return new Uint8Array(await file.arrayBuffer());
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(file);
  });
}

export const abortError = () => new DOMException("The upload was stopped.", "AbortError");

/** SHA-256 of `file`, hex. `onBytes` hears how many bytes are done; `signal` stops it with an AbortError. */
export async function sha256Stream(file: Blob, chunkSize: number, signal?: AbortSignal, onBytes?: (done: number) => void): Promise<string> {
  const hasher = sha256.create();
  let done = 0;
  for (const [start, end] of chunkRanges(file.size, chunkSize)) {
    if (signal?.aborted) throw abortError();
    hasher.update(await bytesOf(file.slice(start, end)));
    done = end;
    onBytes?.(done);
  }
  if (signal?.aborted) throw abortError();
  return hex(hasher.digest());
}
