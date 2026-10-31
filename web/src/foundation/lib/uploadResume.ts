// Picking an upload up again after a refresh. The server is asked first (access is checked there), and what to do
// follows its status: an open upload asks for the same file again and sends only the parts the server lacks.
import { ApiError, api, unwrap } from "../platform-client";
import { resumeFrom, type UploadOptions, type UploadView } from "./chunkedUpload";
import { listUploads, removeUpload, type StoredUpload } from "./uploadStore";

export type Resumable =
  | { state: "needs_file"; record: StoredUpload; view: UploadView | null }
  | { state: "finalizing"; record: StoredUpload; view: UploadView }
  | { state: "complete"; record: StoredUpload; documentId: string };

/** The first upload of this person on this case that can be carried on, or null (the others are dropped). */
export async function findResumable(personId: string, caseId: string): Promise<Resumable | null> {
  for (const record of listUploads(personId, caseId)) {
    if (Date.parse(record.expiresAt) <= Date.now()) {
      removeUpload(personId, caseId, record.uploadId);
      continue;
    }
    // Written before the start request was answered: the start is asked again with the same key when the file is back.
    if (!record.uploadId) return { state: "needs_file", record, view: null };
    let view: UploadView;
    try {
      view = await unwrap(api.GET("/v1/uploads/{upload_id}", { params: { path: { upload_id: record.uploadId } } }));
    } catch (err) {
      // Gone or not ours: drop it. A dropped connection leaves it for the next visit.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) removeUpload(personId, caseId, record.uploadId);
      continue;
    }
    if (view.status === "open") return { state: "needs_file", record, view };
    if (view.status === "finalizing") return { state: "finalizing", record, view };
    if (view.status === "complete" && view.document_id) {
      removeUpload(personId, caseId, record.uploadId);
      return { state: "complete", record, documentId: view.document_id };
    }
    removeUpload(personId, caseId, record.uploadId);
  }
  return null;
}

/** Throws `upload_wrong_file` for a different file (the record is kept); otherwise finishes and returns the document id. */
export function resumeUpload(caseId: string, found: Resumable, file: File | null, options: UploadOptions = {}): Promise<string> {
  return resumeFrom(caseId, found, file, options);
}

/** Gives up on a remembered upload: asks the server to cancel it (best effort) and forgets it. */
export async function discardResumable(personId: string, caseId: string, record: StoredUpload): Promise<void> {
  removeUpload(personId, caseId, record.uploadId);
  if (!record.uploadId) return;
  try {
    await unwrap(api.POST("/v1/uploads/{upload_id}/cancel", { params: { path: { upload_id: record.uploadId } }, headers: { "Idempotency-Key": record.keys.cancelKey } }));
  } catch {
    /* the nightly sweep clears what is left */
  }
}
