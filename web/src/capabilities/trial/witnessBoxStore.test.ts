// The S25 copy is kept on the device in IndexedDB, still sealed, so it is there after the page is loaded
// again (no connection needed), and removing it deletes it from the device. Its key is kept only wrapped
// by a key made from the expert's PIN (PBKDF2-SHA256, at least 600,000 rounds, random salt), so what is
// on the device opens nothing without the PIN; five wrong PINs wipe it (threat model T10).
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, expect, test, vi } from "vitest";
import { CASE, copyContent, PIN, sealed } from "./testing";

const META = { label: "Laptop", locale: "en-GB", expiresAt: "2099-01-01T00:00:00Z", kinds: {} };
const SLOW = 60_000; // each PIN is stretched 600,000 times on purpose

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

const bytesOf = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
const contains = (hay: Uint8Array, needle: Uint8Array) => {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return true;
  }
  return false;
};

test("the copy is still there after the page is loaded again, and opens with the PIN", async () => {
  const content = copyContent();
  await (await newPage()).saveCopy(await sealed(content), META, PIN);
  const page = await newPage();
  expect(await page.findCopy(CASE)).toMatchObject({ state: "locked", triesLeft: 5 });
  const opened = await page.unlockCopy(CASE, PIN);
  expect(opened.state).toBe("open");
  if (opened.state === "open") expect(opened.content).toEqual(content);
}, SLOW);

test("the device holds no key that opens the copy: only a key wrapped by the PIN, and a salt", async () => {
  const content = copyContent();
  const download = await sealed(content);
  await (await newPage()).saveCopy(download, META, PIN);
  const kept = await onDevice(CASE);
  expect(kept).toBeDefined();
  expect(Object.keys(kept!).sort()).toEqual(
    ["caseId", "ciphertext", "failedTries", "formatVersion", "id", "iterations", "iv", "meta", "salt", "wrapIv", "wrappedKey"],
  );
  expect(Object.values(kept!).some((v) => typeof CryptoKey !== "undefined" && v instanceof CryptoKey)).toBe(false);
  expect(kept!.iterations as number).toBeGreaterThanOrEqual(600_000);
  expect((kept!.salt as Uint8Array).length).toBeGreaterThanOrEqual(16);
  const raw = bytesOf(download.key);
  for (const value of Object.values(kept!)) if (value instanceof Uint8Array) expect(contains(value, raw)).toBe(false);
  expect(new TextDecoder().decode(kept!.ciphertext as Uint8Array)).not.toContain(content.court_bundle);
  // The wrapped key on its own is not an AES key that opens the copy.
  const wrapped = kept!.wrappedKey as Uint8Array;
  const asKey = await crypto.subtle.importKey("raw", wrapped.slice(0, 32), "AES-GCM", false, ["decrypt"]);
  const additionalData = new TextEncoder().encode(`avodeck-offline:${download.id}:${download.format_version}`);
  await expect(
    crypto.subtle.decrypt({ name: "AES-GCM", iv: kept!.iv as Uint8Array, additionalData }, asKey, kept!.ciphertext as Uint8Array),
  ).rejects.toThrow();
}, SLOW);

test("a wrong PIN does not open the copy, and the tries left are kept on the device", async () => {
  await (await newPage()).saveCopy(await sealed(), META, PIN);
  expect(await (await newPage()).unlockCopy(CASE, "135790")).toEqual({ state: "wrong", triesLeft: 4 });
  const later = await newPage();
  expect(await later.findCopy(CASE)).toMatchObject({ state: "locked", triesLeft: 4 });
  expect((await later.unlockCopy(CASE, PIN)).state).toBe("open");
  expect(await later.findCopy(CASE)).toMatchObject({ state: "locked", triesLeft: 5 });
}, SLOW);

test("the fifth wrong PIN wipes the copy from the device", async () => {
  const page = await newPage();
  await page.saveCopy(await sealed(), META, PIN);
  for (const left of [4, 3, 2, 1]) expect(await page.unlockCopy(CASE, "000000")).toEqual({ state: "wrong", triesLeft: left });
  expect(await page.unlockCopy(CASE, "000000")).toEqual({ state: "wiped" });
  expect(await onDevice(CASE)).toBeUndefined();
  expect(await (await newPage()).unlockCopy(CASE, PIN)).toEqual({ state: "none" });
}, SLOW);

test("a PIN shorter than six numbers is refused before anything is kept", async () => {
  const page = await newPage();
  await expect(page.saveCopy(await sealed(), META, "1234")).rejects.toThrow();
  expect(await onDevice(CASE)).toBeUndefined();
});

test("removing the copy deletes it from the device", async () => {
  const page = await newPage();
  await page.saveCopy(await sealed(), META, PIN);
  await page.forgetCopy(CASE);
  expect(await onDevice(CASE)).toBeUndefined();
  expect(await (await newPage()).findCopy(CASE)).toEqual({ state: "none" });
}, SLOW);

test("a copy past its end date is wiped from the device when it is looked for", async () => {
  await (await newPage()).saveCopy(await sealed(), { ...META, expiresAt: "2020-01-01T00:00:00Z" }, PIN);
  expect(await (await newPage()).findCopy(CASE)).toEqual({ state: "expired" });
  expect(await onDevice(CASE)).toBeUndefined();
}, SLOW);
