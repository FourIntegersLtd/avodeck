import type { Schemas } from "../../foundation/platform-client";

export type ConsultEntry = Schemas["Entry"];

/** Facts on the visit page vs elsewhere in the bundle. */
export function splitByVisitPage(items: ConsultEntry[], visitPage: number | null | undefined) {
  if (visitPage == null) return { onPage: items, otherPages: [] as ConsultEntry[] };
  const onPage: ConsultEntry[] = [];
  const otherPages: ConsultEntry[] = [];
  for (const e of items) {
    if (e.page_no === visitPage) onPage.push(e);
    else otherPages.push(e);
  }
  return { onPage, otherPages };
}

/** Other-page facts grouped by bundle page number, ascending. */
export function groupByPage(items: ConsultEntry[]): { page: number; items: ConsultEntry[] }[] {
  const map = new Map<number, ConsultEntry[]>();
  for (const e of items) {
    const p = e.page_no ?? 0;
    const list = map.get(p) ?? [];
    list.push(e);
    map.set(p, list);
  }
  return [...map.entries()]
    .sort(([a], [b]) => a - b)
    .map(([page, pageItems]) => ({ page, items: pageItems }));
}
