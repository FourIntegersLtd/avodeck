// Sends a clearer copy of a page to the API in parts, as later records, and returns the document it became.
import type { UploadOptions } from "../../foundation/lib/chunkedUpload";
import { uploadInParts } from "../../foundation/lib/upload";

export const uploadLaterRecords = (caseId: string, file: File, options?: UploadOptions): Promise<string> => uploadInParts(caseId, file, "records", true, options);
