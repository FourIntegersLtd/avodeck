// The page's picture, with anything drawn over it placed in page fractions.
import { useEffect, useState, type ReactNode } from "react";
import { pageImage } from "./pageImageCache";
import { w } from "./words";

export function PageImage({ caseId, pageNo, zoom = 100, children }: { caseId: string; pageNo: number; zoom?: number; children?: ReactNode }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSrc(null);
    pageImage(caseId, pageNo)
      .then((url) => live && setSrc(url))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [caseId, pageNo]);
  const alt = w("read.page_alt", { page: pageNo });
  return (
    <div className="rd-paper" tabIndex={0} aria-label={alt}>
      <div className="rd-page" style={{ width: `${zoom}%` }}>
        {src ? <img className="rd-page__image" src={src} alt={alt} /> : <div className="rd-page__image rd-page__image--empty" role="img" aria-label={alt} />}
        {src && children}
      </div>
    </div>
  );
}
