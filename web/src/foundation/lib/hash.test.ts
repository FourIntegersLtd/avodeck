// The streaming hash (pure JavaScript, no WebAssembly) agrees with a whole-file hash at every part size.
import { createHash } from "node:crypto";
import { expect, test } from "vitest";
import { hashFile } from "./chunkedUpload";

const data = Uint8Array.from({ length: 1000 }, (_, i) => (i * 131 + 7) % 256);
const whole = createHash("sha256").update(data).digest("hex");

test.each([1, 7, 64, 999, 1000, 4096])("hashing in %i-byte parts equals the whole-file hash", async (size) => {
  expect(await hashFile(new Blob([data]), size)).toBe(whole);
});

test("an empty file has the well-known empty hash", async () => {
  expect(await hashFile(new Blob([]), 8)).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
});

test("hashing can be cancelled", async () => {
  const stop = new AbortController();
  stop.abort();
  await expect(hashFile(new Blob([data]), 7, stop.signal)).rejects.toMatchObject({ name: "AbortError" });
});
