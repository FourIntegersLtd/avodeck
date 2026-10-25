import { useEffect, useState } from "react";
import { api } from "../../foundation/platform-client";

async function dataUrl(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:${blob.type || "image/jpeg"};base64,${btoa(binary)}`;
}

/** A page's small picture, fetched from FastAPI and shown as a data: URL (the CSP allows no other image origin).
 * `full` asks for the whole page, large enough to read; a page the API no longer serves whole falls back to the small one. */
export function Thumbnail({ caseId, pageNo, alt, className = "pl-thumb", full = false }: { caseId: string; pageNo: number; alt: string; className?: string; full?: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const params = { path: { case_id: caseId, page_no: pageNo } };
    const small = () => api.GET("/v1/cases/{case_id}/pages/{page_no}/thumbnail", { params, parseAs: "blob" });
    (full ? api.GET("/v1/cases/{case_id}/pages/{page_no}/image", { params, parseAs: "blob" }).then((r) => (r.data ? r : small())) : small())
      .then(async ({ data }) => (data ? dataUrl(data as Blob) : null))
      .then((url) => live && url && setSrc(url))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [caseId, pageNo, full]);
  return src ? <img className={className} src={src} alt={alt} /> : <span className={`${className} pl-thumb--empty`} role="img" aria-label={alt} />;
}
