/** How far apart dots must be (percent of track) before a date fits under the dot (~132px label at 720px). */
export const MIN_LABEL_GAP_PCT = 18;
export const MAX_LABELS_PER_LANE = 4;
/** Above this count the swim lane is dots-only; use the list below. */
export const TRACK_LABELS_UP_TO = 4;

export type Placed = { id: string; left: number };

/** Which dots may show a short date under the dot (sparse lanes only). */
export function labelledDotIds(placed: Placed[]): Set<string> {
  if (placed.length > TRACK_LABELS_UP_TO) return new Set();
  const sorted = [...placed].sort((a, b) => a.left - b.left);
  const allowed = new Set<string>();
  let lastLabelled = -Infinity;
  for (const p of sorted) {
    if (allowed.size >= MAX_LABELS_PER_LANE) break;
    if (p.left - lastLabelled >= MIN_LABEL_GAP_PCT) {
      allowed.add(p.id);
      lastLabelled = p.left;
    }
  }
  return allowed;
}

export type MonthMark = { at: number; label: string };

/** Fewer month ticks on a long whole-record span so headers do not stack. */
export function thinMonthMarks(marks: MonthMark[], zoom: "whole" | "month"): MonthMark[] {
  if (zoom === "month" || marks.length <= 10) return marks;
  const out: MonthMark[] = [];
  for (let i = 0; i < marks.length; i++) {
    const m = marks[i]!;
    const month = new Date(m.at).getUTCMonth();
    if (i === 0 || month === 0 || i % 3 === 0) out.push(m);
  }
  return out.length > 0 ? out : marks;
}
