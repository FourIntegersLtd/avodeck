// The copy for the witness box (S25), kept on this device (threat model T10). The API seals it with AES-GCM
// and hands over the key once. Here that key is wrapped at once by a key made from a PIN the expert
// chooses (PBKDF2-SHA256, PIN_ITERATIONS rounds, a random salt), and only the wrapped key and the salt are
// kept beside the sealed bytes: what is on the device opens nothing without the PIN. Five wrong PINs wipe
// it. When online, the status check wipes a copy that was removed, has ended or is unknown; with no network
// the copy is kept (courts often have no wifi). Read-only (rule 9).
import { api, type Schemas } from "../../foundation/platform-client";
import type { CopyContent } from "./copy";
import { deviceStore } from "./witnessBoxStore";

export const PIN_ITERATIONS = 600_000;
export const PIN_TRIES = 5;
export const PIN_PATTERN = /^\d{6,12}$/;
const CHECK_TIMEOUT_MS = 8_000;

export interface CopyMeta {
  label: string;
  locale: string;
  expiresAt: string;
  kinds: Record<string, string>;
}

export interface StoredCopy {
  caseId: string;
  id: string;
  formatVersion: number;
  /** The copy's AES key, wrapped (AES-GCM) by the key made from the PIN. Never the key itself. */
  wrappedKey: Uint8Array;
  wrapIv: Uint8Array;
  salt: Uint8Array;
  iterations: number;
  failedTries: number;
  iv: Uint8Array;
  /** A typed array, not a bare ArrayBuffer: Node 20's WebCrypto refuses an ArrayBuffer from another realm (jsdom). */
  ciphertext: Uint8Array;
  meta: CopyMeta;
}

export interface CopyStore {
  get(caseId: string): Promise<StoredCopy | undefined>;
  put(copy: StoredCopy): Promise<void>;
  remove(caseId: string): Promise<void>;
}

export type Found = { state: "none" } | { state: "expired" } | { state: "locked"; packageId: string; triesLeft: number; expiresAt: string; locale: string };
export type Opened =
  | { state: "none" }
  | { state: "wrong"; triesLeft: number }
  | { state: "wiped" }
  | { state: "broken" }
  | { state: "open"; content: CopyContent; meta: CopyMeta };

const FORMAT_VERSION = 1;

/** Keeps copies in this tab's memory (tests, and browsers with no IndexedDB). */
export function memoryStore(): CopyStore {
  const copies = new Map<string, StoredCopy>();
  return {
    get: async (caseId) => copies.get(caseId),
    put: async (copy) => void copies.set(copy.caseId, copy),
    remove: async (caseId) => void copies.delete(caseId),
  };
}

let store: CopyStore | null = null;

/** Chooses where copies are kept: the device (`witnessBoxStore.ts`) by default; tests may use `memoryStore()`. */
export function setCopyStore(next: CopyStore): void {
  store = next;
}

function current(): CopyStore {
  if (!store) store = deviceStore() ?? memoryStore();
  return store;
}

const bytes = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
const aad = (id: string, formatVersion: number) => new TextEncoder().encode(`avodeck-offline:${id}:${formatVersion}`);

async function pinKey(pin: string, salt: Uint8Array, iterations: number): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(pin), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt: salt as BufferSource, iterations },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["wrapKey", "unwrapKey"],
  );
}

/** Keeps the one-time download on this device, still sealed, its key wrapped by the PIN. */
export async function saveCopy(download: Schemas["Download"], meta: CopyMeta, pin: string): Promise<void> {
  if (!PIN_PATTERN.test(pin)) throw new Error("pin_too_short");
  const raw = bytes(download.key);
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", true, ["decrypt"]);
  raw.fill(0);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const wrapIv = crypto.getRandomValues(new Uint8Array(12));
  const wrapper = await pinKey(pin, salt, PIN_ITERATIONS);
  const wrappedKey = new Uint8Array(await crypto.subtle.wrapKey("raw", key, wrapper, { name: "AES-GCM", iv: wrapIv }));
  await current().put({
    caseId: download.case_id,
    id: download.id,
    formatVersion: download.format_version,
    wrappedKey,
    wrapIv,
    salt,
    iterations: PIN_ITERATIONS,
    failedTries: 0,
    iv: bytes(download.iv),
    ciphertext: bytes(download.ciphertext),
    meta,
  });
}

export async function hasCopy(caseId: string): Promise<boolean> {
  return (await current().get(caseId)) !== undefined;
}

export async function forgetCopy(caseId: string): Promise<void> {
  await current().remove(caseId);
}

/** Looks for the copy without opening it: wiped if past its end date. */
export async function findCopy(caseId: string, now: Date = new Date()): Promise<Found> {
  const copy = await current().get(caseId);
  if (!copy) return { state: "none" };
  if (new Date(copy.meta.expiresAt).getTime() <= now.getTime()) {
    await current().remove(caseId);
    return { state: "expired" };
  }
  return { state: "locked", packageId: copy.id, triesLeft: PIN_TRIES - copy.failedTries, expiresAt: copy.meta.expiresAt, locale: copy.meta.locale };
}

/**
 * Asks the API whether the copy may still be opened, when there is a connection. Wipes it on revoked,
 * expired or 404. Keeps it when offline, on a network failure, or on any other answer (signed out, an error).
 */
export async function checkWithServer(caseId: string, packageId: string): Promise<"kept" | "wiped"> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return "kept";
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), CHECK_TIMEOUT_MS);
  try {
    const { data, response } = await api.GET("/v1/cases/{case_id}/offline-packages/{package_id}/status", {
      params: { path: { case_id: caseId, package_id: packageId } },
      signal: timeout.signal,
    });
    const gone = response.status === 404 || data?.status === "revoked" || data?.status === "expired";
    if (!gone) return "kept";
    await current().remove(caseId);
    return "wiped";
  } catch {
    return "kept";
  } finally {
    clearTimeout(timer);
  }
}

/** Opens the copy with the PIN. A wrong PIN counts against it; the fifth wipes the copy. */
export async function unlockCopy(caseId: string, pin: string): Promise<Opened> {
  const copy = await current().get(caseId);
  if (!copy) return { state: "none" };
  let key: CryptoKey;
  try {
    const wrapper = await pinKey(pin, copy.salt, copy.iterations);
    key = await crypto.subtle.unwrapKey(
      "raw",
      copy.wrappedKey as BufferSource,
      wrapper,
      { name: "AES-GCM", iv: copy.wrapIv as BufferSource },
      "AES-GCM",
      false,
      ["decrypt"],
    );
  } catch {
    const failedTries = copy.failedTries + 1;
    if (failedTries >= PIN_TRIES) {
      await current().remove(caseId);
      return { state: "wiped" };
    }
    await current().put({ ...copy, failedTries });
    return { state: "wrong", triesLeft: PIN_TRIES - failedTries };
  }
  if (copy.failedTries !== 0) await current().put({ ...copy, failedTries: 0 });
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: copy.iv as BufferSource, additionalData: aad(copy.id, copy.formatVersion) },
      key,
      copy.ciphertext as BufferSource,
    );
    const content = JSON.parse(new TextDecoder().decode(plain)) as CopyContent;
    if (content.format_version !== FORMAT_VERSION || content.case_id !== caseId) return { state: "broken" };
    return { state: "open", content, meta: copy.meta };
  } catch {
    return { state: "broken" };
  }
}
