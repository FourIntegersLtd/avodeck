// Numbers, dates, language names and page lists in the case's locale (Intl), for display only.
import { formatDay, formatList, formatNumber as formatNumberIn, safeLocale } from "../../foundation/i18n/dates";
import { w } from "./words";

const locale = safeLocale;

export const formatNumber = (n: number, tag: string | null | undefined): string => formatNumberIn(n, tag);

/** A calendar date (YYYY-MM-DD) as day, short month and year. */
export const formatDate = (iso: string, tag: string | null | undefined): string => formatDay(iso, tag, "medium");

/** A language's name ("cy" -> "Welsh"), or the code when the browser does not know it. */
export function languageName(code: string, tag: string | null | undefined): string {
  try {
    return new Intl.DisplayNames([locale(tag)], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

const joinList = (items: string[], tag: string | null | undefined): string => formatList(items, tag);

/** Page numbers as words: "page 903", "pages 412 to 415", "pages 3, 7 and 20 to 22". */
export function pageList(pages: number[], tag: string | null | undefined): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  const runs: Array<[number, number]> = [];
  for (const p of sorted) {
    const last = runs[runs.length - 1];
    if (last && p === last[1] + 1) last[1] = p;
    else runs.push([p, p]);
  }
  const parts = runs.map(([from, to]) =>
    from === to ? formatNumber(from, tag) : w("pages.range", { from: formatNumber(from, tag), to: formatNumber(to, tag) }),
  );
  return w("pages.list", { count: sorted.length, list: joinList(parts, tag) });
}
