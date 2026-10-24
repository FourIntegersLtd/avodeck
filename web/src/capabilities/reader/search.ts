// What the page viewer holds for the visit, beyond fit and zoom (viewer.ts): the last search of the
// bundle's words, two pages side by side and full screen. Held in this tab's memory only (browser storage
// is off limits in capabilities), so it lasts from page to page, not past a reload.
import type { Schemas } from "../../foundation/platform-client";

export type SearchResult = { q: string; pages: Schemas["SearchPage"][]; total: number };
export interface Tools {
  twoPages: boolean;
  full: boolean;
}

const NO_TOOLS: Tools = { twoPages: false, full: false };
let results: Record<string, SearchResult> = {};
let tools: Tools = NO_TOOLS;

/** At least two letters or numbers to look for (the server says the same). */
export const searchable = (q: string) => (q.match(/[\p{L}\p{N}]/gu) ?? []).length >= 2;

export const loadSearch = (caseId: string): SearchResult | null => results[caseId] ?? null;

export function saveSearch(caseId: string, result: SearchResult | null) {
  const rest = Object.fromEntries(Object.entries(results).filter(([k]) => k !== caseId));
  results = result ? { ...rest, [caseId]: result } : rest;
}

export const loadTools = (): Tools => tools;

export function saveTools(next: Tools) {
  tools = { twoPages: next.twoPages, full: next.full };
}

/** Back to nothing found and no tools on (signing out, or a test). */
export function forgetSearch() {
  results = {};
  tools = NO_TOOLS;
}
