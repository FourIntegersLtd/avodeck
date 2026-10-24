// Every page of the bundle, read or not, for the page strip and "Go to page". Up to 4,000 pages arrive a
// block of 1,000 at a time and are kept for a few minutes, so turning pages does not ask for them again.
import { useEffect, useState } from "react";
import { api, unwrap } from "../../foundation/platform-client";

export interface Listed {
  page_no: number;
  read: boolean;
}

const BLOCK = 1000;
const FRESH_MS = 5 * 60_000;
const kept = new Map<string, { at: number; pages: Promise<Listed[]> }>();

async function fetchAll(caseId: string): Promise<Listed[]> {
  const out: Listed[] = [];
  let cursor: number | undefined;
  for (let block = 0; block < 100; block++) {
    const r = await unwrap(api.GET("/v1/cases/{case_id}/pages", { params: { path: { case_id: caseId }, query: { limit: BLOCK, ...(cursor === undefined ? {} : { cursor }) } } }));
    for (const p of r.pages) out.push({ page_no: p.page_no, read: p.read });
    if (r.next_cursor === null || r.next_cursor === undefined) break;
    cursor = r.next_cursor;
  }
  return out;
}

export function bundlePages(caseId: string): Promise<Listed[]> {
  const seen = kept.get(caseId);
  if (seen && Date.now() - seen.at < FRESH_MS) return seen.pages;
  const pages = fetchAll(caseId);
  kept.set(caseId, { at: Date.now(), pages });
  pages.catch(() => kept.get(caseId)?.pages === pages && kept.delete(caseId));
  return pages;
}

/** The page was just read here: the kept list says so too. */
export function noteRead(caseId: string, pageNo: number) {
  void kept.get(caseId)?.pages.then((pages) => {
    const page = pages.find((p) => p.page_no === pageNo);
    if (page) page.read = true;
  }).catch(() => undefined);
}

export function forgetBundlePages() {
  kept.clear();
}

/** The bundle's pages, or null while they load (or when they could not be loaded). */
export function useBundlePages(caseId: string): Listed[] | null {
  const [pages, setPages] = useState<Listed[] | null>(null);
  useEffect(() => {
    let live = true;
    bundlePages(caseId)
      .then((list) => live && setPages(list))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [caseId]);
  return pages;
}
