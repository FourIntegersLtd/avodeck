// Sends a file to FastAPI in parts and returns the document it became. Shared by the screens that add
// later records (reader: clearer copy; pipeline: add more papers). Files go only to the API (rule 4).
import { api, unwrap } from "../platform-client";

async function bytesOf(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as ArrayBuffer);
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(file);
  });
}

const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

/** Uploads `file` as `kind`; `append` marks it as later records added to a case that already has a bundle. */
export async function uploadInParts(caseId: string, file: File, kind: string, append = false): Promise<string> {
  const bytes = await bytesOf(file);
  const sha256 = hex(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)));
  const started = await unwrap(
    api.POST("/v1/cases/{case_id}/uploads", {
      params: { path: { case_id: caseId } },
      body: { filename: file.name, kind, sha256, size: bytes.byteLength, ...(append ? { append: true } : {}) },
    }),
  );
  for (let n = 1; n <= started.chunk_count; n += 1) {
    await unwrap(
      api.PUT("/v1/uploads/{upload_id}/chunks/{n}", {
        params: { path: { upload_id: started.upload_id, n } },
        body: bytes.slice((n - 1) * started.chunk_size, n * started.chunk_size) as never,
        bodySerializer: (b: unknown) => b as BodyInit,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );
  }
  const done = await unwrap(api.POST("/v1/uploads/{upload_id}/complete", { params: { path: { upload_id: started.upload_id } } }));
  return String(done.document_id);
}
