/** Time-window presets for S11: whole record, instruction period, or custom dates. */

export type TimelineWindowPreset = "whole" | "period" | "custom";

export type DateRange = { first: string; last: string };

const ISO = /^\d{4}-\d{2}-\d{2}$/;

export function isIsoDate(s: string): boolean {
  return ISO.test(s);
}

/** Case instruction period when both ends are set. */
export function instructionPeriod(caseRow: { period_start?: string | null; period_end?: string | null } | null | undefined): { from: string; to: string } | null {
  const from = caseRow?.period_start ?? "";
  const to = caseRow?.period_end ?? "";
  if (!isIsoDate(from) || !isIsoDate(to)) return null;
  return { from, to };
}

/** Default window when there is no instruction period: last two calendar years ending at range.last. */
export function lastTwoYearsWindow(range: DateRange): { from: string; to: string } {
  const end = parseUtc(range.last);
  const start = new Date(Date.UTC(end.getUTCFullYear() - 2, end.getUTCMonth(), end.getUTCDate()));
  const first = parseUtc(range.first);
  const fromDate = start.getTime() < first.getTime() ? first : start;
  return { from: isoDay(fromDate), to: range.last };
}

export function periodDefaultDates(
  caseRow: { period_start?: string | null; period_end?: string | null } | null | undefined,
  recordRange: DateRange | null | undefined,
): { from: string; to: string } | null {
  const instruction = instructionPeriod(caseRow);
  if (instruction) return instruction;
  if (recordRange?.first && recordRange?.last && isIsoDate(recordRange.first) && isIsoDate(recordRange.last)) {
    return lastTwoYearsWindow(recordRange);
  }
  return null;
}

function parseUtc(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1));
}

function isoDay(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
