// A page's picture for S12: the bytes FastAPI streams become an object URL (no base64 copy of the page in
// memory), and the last few pages are kept so flipping back does not fetch and decrypt the page again.
// Nothing is written to disk and nothing outlives the tab: the server's no-store stays honoured.
import { api, unwrap } from "../../foundation/platform-client";

/** How many pages are kept; a page picture is a few hundred KB, so this stays small. */
export const KEEP_PAGES = 6;

interface Kept {
  url: Promise<string>;
  made?: string; // set once the picture has arrived
}

const kept = new Map<string, Kept>();

async function fetchPage(caseId: string, pageNo: number): Promise<string> {
  const blob = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/image", { params: { path: { case_id: caseId, page_no: pageNo } }, parseAs: "blob" }));
  return URL.createObjectURL(blob as Blob);
}

function release(page: Kept) {
  if (page.made) URL.revokeObjectURL(page.made);
  else page.url.then((made) => URL.revokeObjectURL(made)).catch(() => undefined);
}

/** The object URL for a page, fetched once and kept while it is among the last `KEEP_PAGES` pages looked at. */
export function pageImage(caseId: string, pageNo: number): Promise<string> {
  const key = `${caseId}/${pageNo}`;
  const seen = kept.get(key);
  if (seen) {
    kept.delete(key);
    kept.set(key, seen); // the most recently used goes last
    return seen.url;
  }
  const page: Kept = { url: fetchPage(caseId, pageNo) };
  kept.set(key, page);
  page.url.then(
    (made) => {
      page.made = made;
    },
    () => {
      if (kept.get(key) === page) kept.delete(key);
    },
  );
  for (const [oldest, old] of kept) {
    if (kept.size <= KEEP_PAGES) break;
    kept.delete(oldest);
    release(old);
  }
  return page.url;
}

/** Releases every kept page (leaving the case, or a test). */
export function forgetPages() {
  for (const page of kept.values()) release(page);
  kept.clear();
}
