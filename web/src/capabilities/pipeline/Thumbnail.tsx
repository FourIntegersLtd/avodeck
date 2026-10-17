import { useEffect, useState } from "react";
import { api } from "../../foundation/platform-client";

async function dataUrl(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:${blob.type || "image/jpeg"};base64,${btoa(binary)}`;
}

/** A page's small picture, fetched from FastAPI and shown as a data: URL (the CSP allows no other image origin). */
export function Thumbnail({ caseId, pageNo, alt }: { caseId: string; pageNo: number; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .GET("/v1/cases/{case_id}/pages/{page_no}/thumbnail", { params: { path: { case_id: caseId, page_no: pageNo } }, parseAs: "blob" })
      .then(async ({ data }) => (data ? dataUrl(data as Blob) : null))
      .then((url) => live && url && setSrc(url))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [caseId, pageNo]);
  return src ? <img className="pl-thumb" src={src} alt={alt} /> : <span className="pl-thumb pl-thumb--empty" role="img" aria-label={alt} />;
}
