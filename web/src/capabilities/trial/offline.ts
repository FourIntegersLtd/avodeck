// The copy for the witness box (S25), kept on this device. The API seals it with AES-GCM and hands over
// the key once; the key is kept as a non-extractable CryptoKey next to the sealed bytes, so the copy can
// be opened with no connection but never read out as plain text from storage. Read-only (rule 9).
import type { Schemas } from "../../foundation/platform-client";
import type { CopyContent } from "./copy";
import { deviceStore } from "./witnessBoxStore";

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
  key: CryptoKey;
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

export type Opened = { state: "none" } | { state: "expired" } | { state: "broken" } | { state: "open"; content: CopyContent; meta: CopyMeta };

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

/** Keeps the one-time download on this device, still sealed. */
export async function saveCopy(download: Schemas["Download"], meta: CopyMeta): Promise<void> {
  const key = await crypto.subtle.importKey("raw", bytes(download.key), "AES-GCM", false, ["decrypt"]);
  await current().put({
    caseId: download.case_id,
    id: download.id,
    formatVersion: download.format_version,
    key,
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

/** Opens the copy for a case: wiped if past its end date, refused if it does not unseal. */
export async function openCopy(caseId: string, now: Date = new Date()): Promise<Opened> {
  const copy = await current().get(caseId);
  if (!copy) return { state: "none" };
  if (new Date(copy.meta.expiresAt).getTime() <= now.getTime()) {
    await current().remove(caseId);
    return { state: "expired" };
  }
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: copy.iv as BufferSource, additionalData: aad(copy.id, copy.formatVersion) },
      copy.key,
      copy.ciphertext as BufferSource,
    );
    const content = JSON.parse(new TextDecoder().decode(plain)) as CopyContent;
    if (content.format_version !== FORMAT_VERSION || content.case_id !== caseId) return { state: "broken" };
    return { state: "open", content, meta: copy.meta };
  } catch {
    return { state: "broken" };
  }
}
