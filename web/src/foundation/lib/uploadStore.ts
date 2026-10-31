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
}

const PREFIX = "av.upload.";
export const uploadKey = (personId: string, caseId: string, uploadId: string | null) => `${PREFIX}${personId}.${caseId}.${uploadId ?? "new"}`;

export function saveUpload(personId: string, caseId: string, record: StoredUpload): void {
  try {
    // Only the named fields are written, whatever else the object carries.
    const { uploadId, size, lastModified, sha256, keys, expiresAt } = record;
    localStorage.setItem(uploadKey(personId, caseId, uploadId), JSON.stringify({ uploadId, size, lastModified, sha256, keys, expiresAt }));
  } catch {
    /* storage may be unavailable: the upload still runs, it just cannot be resumed */
  }
}

export function removeUpload(personId: string, caseId: string, uploadId: string | null): void {
  try {
    localStorage.removeItem(uploadKey(personId, caseId, uploadId));
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
        if (r && typeof r.size === "number" && typeof r.sha256 === "string" && r.keys?.startKey && typeof r.expiresAt === "string") found.push(r);
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
