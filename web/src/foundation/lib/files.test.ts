import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { bytesOf, saveBlob } from "./files";

const realCreate = URL.createObjectURL;
const realRevoke = URL.revokeObjectURL;
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  URL.createObjectURL = realCreate;
  URL.revokeObjectURL = realRevoke;
});

test("bytesOf reads a blob's bytes", async () => {
  vi.useRealTimers();
  const bytes = new Uint8Array(await bytesOf(new Blob(["abc"])));
  expect(Array.from(bytes)).toEqual([97, 98, 99]);
});

test("saveBlob clicks a download link with the name, then lets go of the address", () => {
  const create = vi.fn(() => "blob:x");
  const revoke = vi.fn();
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
  let name = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    name = this.download;
  });
  saveBlob(new Blob(["x"]), "case.zip");
  expect(name).toBe("case.zip");
  expect(revoke).not.toHaveBeenCalled();
  vi.advanceTimersByTime(40_000);
  expect(revoke).toHaveBeenCalledWith("blob:x");
  expect(document.querySelector("a[download]")).toBeNull();
});

test("saveBlob does nothing where object addresses do not exist", () => {
  Object.assign(URL, { createObjectURL: undefined });
  expect(() => saveBlob(new Blob(["x"]), "a.pdf")).not.toThrow();
});
