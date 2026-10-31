// What reaches localStorage is exactly the named fields, however much the object handed in carries.
import { beforeEach, expect, test } from "vitest";
import { listUploads, saveUpload, uploadKey, type StoredUpload } from "./uploadStore";

const P = "p1";
const C = "c1";
const ok: StoredUpload = { uploadId: "u1", size: 1, lastModified: 2, sha256: "ab", keys: { startKey: "s", completeKey: "c", cancelKey: "x" }, expiresAt: new Date(Date.now() + 1e6).toISOString(), kind: "records", append: false, slot: "z" };
beforeEach(() => localStorage.clear());

test("saveUpload writes only the named fields, never a name that rides along", () => {
  saveUpload(P, C, { ...ok, name: "secret.pdf", filename: "secret.pdf", token: "t" } as StoredUpload);
  const written = JSON.parse(localStorage.getItem(uploadKey(P, C, "u1"))!) as Record<string, unknown>;
  expect(Object.keys(written).sort()).toEqual(["append", "expiresAt", "keys", "kind", "lastModified", "sha256", "size", "slot", "uploadId"]);
  expect(localStorage.getItem(uploadKey(P, C, "u1"))).not.toContain("secret");
});

test("a record with a corrupted uploadId or lastModified is dropped, not listed", () => {
  localStorage.setItem(`av.upload.${P}.${C}.bad1`, JSON.stringify({ ...ok, uploadId: 42 }));
  localStorage.setItem(`av.upload.${P}.${C}.bad2`, JSON.stringify({ ...ok, lastModified: "x" }));
  expect(listUploads(P, C)).toEqual([]);
  expect(Object.keys(localStorage)).toEqual([]);
});
