// Sends a clearer copy of a page to the API in parts, as later records, and returns the document it became.
import { api, unwrap } from "../../foundation/platform-client";

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

export async function uploadLaterRecords(caseId: string, file: File): Promise<string> {
  const bytes = await bytesOf(file);
  const sha256 = hex(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)));
  const started = await unwrap(
    api.POST("/v1/cases/{case_id}/uploads", { params: { path: { case_id: caseId } }, body: { filename: file.name, kind: "records", sha256, size: bytes.byteLength, append: true } }),
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
