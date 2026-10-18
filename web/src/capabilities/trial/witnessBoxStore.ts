// Where the S25 copy lives on this device: IndexedDB, the one storage the foundation guard allows, and
// only here (web/AGENTS.md, screen 25). It keeps exactly what `offline.ts` hands it: the sealed bytes, the
// IV, the copy's key wrapped by the PIN, and the PIN's salt. No usable key is ever written to the device.
import type { CopyStore, StoredCopy } from "./offline";

export const WITNESS_BOX_DB = "avodeck-witness-box";
export const COPIES = "copies";

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function open(factory: IDBFactory): Promise<IDBDatabase> {
  const req = factory.open(WITNESS_BOX_DB, 1);
  req.onupgradeneeded = () => {
    if (!req.result.objectStoreNames.contains(COPIES)) req.result.createObjectStore(COPIES, { keyPath: "caseId" });
  };
  return request(req);
}

async function using<T>(factory: IDBFactory, mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open(factory);
  try {
    const tx = db.transaction(COPIES, mode);
    const result = await request(run(tx.objectStore(COPIES)));
    await new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    return result;
  } finally {
    db.close();
  }
}

/** The device's store for sealed copies, or null where the browser has no IndexedDB. */
export function deviceStore(): CopyStore | null {
  const factory = typeof indexedDB === "undefined" ? null : indexedDB;
  if (!factory) return null;
  return {
    get: async (caseId) => (await using(factory, "readonly", (s) => s.get(caseId) as IDBRequest<StoredCopy | undefined>)) ?? undefined,
    put: async (copy) => void (await using(factory, "readwrite", (s) => s.put(copy))),
    remove: async (caseId) => void (await using(factory, "readwrite", (s) => s.delete(caseId))),
  };
}
