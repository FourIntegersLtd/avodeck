// What a refresh must not lose of a running upload: ids, size, modified time, the file's hash, the request keys and the
// expiry. Never what the file is called or holds, nor any credential (rule 6). Kept in localStorage; every read and write is guarded.
export interface UploadKeys {
  startKey: string;
  completeKey: string;
  cancelKey: string;
}
export interface StoredUpload {
  /** `null` until the server has answered the start request: the record is written before that request is sent. */
  uploadId: string | null;
  size: number;
  lastModified: number;
  sha256: string;
  keys: UploadKeys;
  expiresAt: string;
  /** What the file is for, so a resumed start sends the same request and only the right screen offers it. */
  kind: string;
  append: boolean;
  /** Names the record before the server has given the upload an id, so uploads started together do not share one. */
  slot: string;
}

const PREFIX = "av.upload.";
export const uploadKey = (personId: string, caseId: string, uploadId: string | null, slot = "") => `${PREFIX}${personId}.${caseId}.${uploadId ?? `new-${slot}`}`;

export function saveUpload(personId: string, caseId: string, record: StoredUpload): void {
  try {
    // Only the named fields are written, whatever else the object carries.
    const { uploadId, size, lastModified, sha256, keys, expiresAt, kind, append, slot } = record;
    localStorage.setItem(uploadKey(personId, caseId, uploadId, slot), JSON.stringify({ uploadId, size, lastModified, sha256, keys, expiresAt, kind, append, slot }));
  } catch {
    /* storage may be unavailable: the upload still runs, it just cannot be resumed */
  }
}

export function removeUpload(personId: string, caseId: string, uploadId: string | null, slot = ""): void {
  try {
    if (uploadId) localStorage.removeItem(uploadKey(personId, caseId, uploadId));
    if (!uploadId || slot) localStorage.removeItem(uploadKey(personId, caseId, null, slot));
  } catch {
    /* nothing to do */
  }
}

/** The records for this person and case, newest expiry last; anything unreadable is dropped. */
export function listUploads(personId: string, caseId: string): StoredUpload[] {
  const found: StoredUpload[] = [];
  try {
    const head = `${PREFIX}${personId}.${caseId}.`;
    for (const k of Object.keys(localStorage).filter((x) => x.startsWith(head))) {
      try {
        const r = JSON.parse(localStorage.getItem(k) ?? "null") as StoredUpload | null;
        const valid =
          r &&
          (r.uploadId === null || typeof r.uploadId === "string") &&
          typeof r.size === "number" &&
          typeof r.lastModified === "number" &&
          typeof r.sha256 === "string" &&
          r.keys?.startKey &&
          typeof r.expiresAt === "string" &&
          typeof r.kind === "string" &&
          typeof r.append === "boolean" &&
          typeof r.slot === "string";
        if (valid) found.push(r);
        else localStorage.removeItem(k);
      } catch {
        localStorage.removeItem(k);
      }
    }
  } catch {
    return [];
  }
  return found.sort((a, b) => a.expiresAt.localeCompare(b.expiresAt));
}
