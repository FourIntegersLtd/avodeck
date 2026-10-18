// The S25 copy is kept on the device in IndexedDB, still sealed, so it is there after the page is
// loaded again (no connection needed), and removing it deletes it from the device.
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, expect, test, vi } from "vitest";
import { CASE, copyContent, sealed } from "./testing";

const META = { label: "Laptop", locale: "en-GB", expiresAt: "2099-01-01T00:00:00Z", kinds: {} };

beforeEach(() => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.resetModules();
});

/** A new page load: every module starts again; only what is on the device remains. */
async function newPage() {
  vi.resetModules();
  return await import("./offline");
}

/** What IndexedDB holds for the case, read straight from the device. */
async function onDevice(caseId: string): Promise<Record<string, unknown> | undefined> {
  const { WITNESS_BOX_DB, COPIES } = await import("./witnessBoxStore");
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(WITNESS_BOX_DB);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  try {
    if (!db.objectStoreNames.contains(COPIES)) return undefined;
    return await new Promise((resolve, reject) => {
      const req = db.transaction(COPIES, "readonly").objectStore(COPIES).get(caseId);
      req.onsuccess = () => resolve(req.result as Record<string, unknown> | undefined);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

test("the copy is still there, and opens, after the page is loaded again", async () => {
  const content = copyContent();
  await (await newPage()).saveCopy(await sealed(content), META);
  const opened = await (await newPage()).openCopy(CASE);
  expect(opened.state).toBe("open");
  if (opened.state === "open") expect(opened.content).toEqual(content);
});

test("the device holds the copy sealed, with a key that cannot be read out", async () => {
  const content = copyContent();
  await (await newPage()).saveCopy(await sealed(content), META);
  const kept = await onDevice(CASE);
  expect(kept).toBeDefined();
  expect(Object.keys(kept!).sort()).toEqual(["caseId", "ciphertext", "formatVersion", "id", "iv", "key", "meta"]);
  expect((kept!.key as CryptoKey).extractable).toBe(false);
  const text = new TextDecoder().decode(kept!.ciphertext as Uint8Array);
  expect(text).not.toContain(content.court_bundle);
});

test("removing the copy deletes it from the device", async () => {
  const page = await newPage();
  await page.saveCopy(await sealed(), META);
  await page.forgetCopy(CASE);
  expect(await onDevice(CASE)).toBeUndefined();
  expect((await (await newPage()).openCopy(CASE)).state).toBe("none");
});

test("a copy past its end date is wiped from the device when it is opened", async () => {
  await (await newPage()).saveCopy(await sealed(), { ...META, expiresAt: "2020-01-01T00:00:00Z" });
  expect((await (await newPage()).openCopy(CASE)).state).toBe("expired");
  expect(await onDevice(CASE)).toBeUndefined();
});
