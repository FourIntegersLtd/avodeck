// Numbers, dates, language names and page lists in the case's locale (Intl), for display only.
import { w } from "./words";

function locale(tag: string | null | undefined): string {
  try {
    return Intl.getCanonicalLocales(tag ?? "en-GB")[0] ?? "en-GB";
  } catch {
    return "en-GB";
  }
}

export function formatNumber(n: number, tag: string | null | undefined): string {
  return new Intl.NumberFormat(locale(tag)).format(n);
}

/** A calendar date (YYYY-MM-DD) as day, short month and year. */
export function formatDate(iso: string, tag: string | null | undefined): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1));
  return new Intl.DateTimeFormat(locale(tag), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}

/** A language's name ("cy" -> "Welsh"), or the code when the browser does not know it. */
export function languageName(code: string, tag: string | null | undefined): string {
  try {
    return new Intl.DisplayNames([locale(tag)], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function joinList(items: string[], tag: string | null | undefined): string {
  try {
    return new Intl.ListFormat(locale(tag), { style: "long", type: "conjunction" }).format(items);
  } catch {
    return items.join(", ");
  }
}

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
