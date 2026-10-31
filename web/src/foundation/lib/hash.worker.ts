// Works out a file's SHA-256 off the main thread so a big bundle does not freeze the page.
import { sha256Stream } from "./hashCore";

export interface HashRequest {
  file: Blob;
  chunkSize: number;
}
export type HashReply = { hash: string } | { progress: number } | { failed: true };

self.onmessage = (event: MessageEvent<HashRequest>) => {
  const { file, chunkSize } = event.data;
  sha256Stream(file, chunkSize, undefined, (done) => (self as unknown as Worker).postMessage({ progress: done } satisfies HashReply))
    .then((hash) => (self as unknown as Worker).postMessage({ hash } satisfies HashReply))
    .catch(() => (self as unknown as Worker).postMessage({ failed: true } satisfies HashReply));
};
