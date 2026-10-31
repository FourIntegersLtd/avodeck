// Sends a file to FastAPI in parts and returns the document it became. Shared by the screens that add
// later records (reader: clearer copy; pipeline: add more papers). Files go only to the API (rule 4).
// The work is in chunkedUpload.ts: per-part and whole-file SHA-256 checks, retries.
export { uploadInParts } from "./chunkedUpload";
