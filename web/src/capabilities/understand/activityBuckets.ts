import type { Schemas } from "../../foundation/platform-client";
import { axisIso } from "./shared";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];

const DAY_MS = 86_400_000;

export type ActivityGranularity = "day" | "week" | "month";

export type ActivityBucket = {
  key: string;
  label: string;
  from: string;
  to: string;
  total: number;
  byLane: Record<string, number>;
};

export type ActivityBucketsResult = {
  buckets: ActivityBucket[];
  granularity: ActivityGranularity;
  undatedCount: number;
  windowFrom: string;
  windowTo: string;
};

const parseUtc = (iso: string): number => {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
};

const isoDay = (ms: number): string => {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

const addDays = (iso: string, days: number): string => isoDay(parseUtc(iso) + days * DAY_MS);

const daysBetween = (from: string, to: string): number => Math.round((parseUtc(to) - parseUtc(from)) / DAY_MS) + 1;

const monthEnd = (iso: string): string => {
  const [y, m] = iso.split("-").map(Number);
  const last = new Date(Date.UTC(y!, m!, 0));
  return isoDay(last.getTime());
};

/** Monday 00:00 UTC of the week containing `iso`. */
const weekStartMonday = (iso: string): string => {
  const d = new Date(parseUtc(iso));
  const dow = d.getUTCDay();
  const diff = dow === 0 ? -6 : 1 - dow;
  return isoDay(d.getTime() + diff * DAY_MS);
};

const SHORT_MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

function bucketLabel(granularity: ActivityGranularity, from: string, to: string): string {
  if (granularity === "month") return SHORT_MONTH.format(new Date(`${from}T00:00:00Z`));
  if (granularity === "day") return SHORT_DAY.format(new Date(`${from}T00:00:00Z`));
  if (from === to) return SHORT_DAY.format(new Date(`${from}T00:00:00Z`));
  return `${SHORT_DAY.format(new Date(`${from}T00:00:00Z`))} – ${SHORT_DAY.format(new Date(`${to}T00:00:00Z`))}`;
}

function chooseGranularity(spanDays: number): ActivityGranularity {
  if (spanDays > 730) return "month";
  if (spanDays > 60) return "week";
  return "day";
}

function buildBuckets(granularity: ActivityGranularity, windowFrom: string, windowTo: string): ActivityBucket[] {
  const buckets: ActivityBucket[] = [];
  if (granularity === "day") {
    for (let cur = windowFrom; cur <= windowTo; cur = addDays(cur, 1)) {
      buckets.push({ key: cur, label: bucketLabel("day", cur, cur), from: cur, to: cur, total: 0, byLane: {} });
    }
    return buckets;
  }
  if (granularity === "week") {
    let cur = weekStartMonday(windowFrom);
    if (cur < windowFrom) cur = weekStartMonday(windowFrom);
    while (cur <= windowTo) {
      const end = addDays(cur, 6);
      const from = cur < windowFrom ? windowFrom : cur;
      const to = end > windowTo ? windowTo : end;
      buckets.push({ key: `${from}_${to}`, label: bucketLabel("week", from, to), from, to, total: 0, byLane: {} });
      cur = addDays(cur, 7);
    }
    return buckets;
  }
  let y = Number(windowFrom.slice(0, 4));
  let m = Number(windowFrom.slice(5, 7));
  const endY = Number(windowTo.slice(0, 4));
  const endM = Number(windowTo.slice(5, 7));
  while (y < endY || (y === endY && m <= endM)) {
    const from = `${y}-${String(m).padStart(2, "0")}-01`;
    const monthLast = monthEnd(from);
    const to = monthLast > windowTo ? windowTo : monthLast;
    const clampedFrom = from < windowFrom ? windowFrom : from;
    buckets.push({
      key: `${clampedFrom}_${to}`,
      label: bucketLabel("month", clampedFrom, to),
      from: clampedFrom,
      to,
      total: 0,
      byLane: {},
    });
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return buckets;
}

function inRange(iso: string, from: string, to: string): boolean {
  return iso >= from && iso <= to;
}

export function activityBuckets(
  lanes: Lane[],
  unplaced: Event[],
  window?: { from?: string; to?: string },
): ActivityBucketsResult {
  let undatedCount = 0;
  const datedLane: { iso: string; laneKey: string }[] = [];
  const datedOther: string[] = [];

  for (const lane of lanes) {
    for (const e of lane.events) {
      const iso = axisIso(e);
      if (!iso) {
        undatedCount += 1;
        continue;
      }
      datedLane.push({ iso, laneKey: lane.key });
    }
  }
  for (const e of unplaced) {
    const iso = axisIso(e);
    if (!iso) {
      undatedCount += 1;
      continue;
    }
    datedOther.push(iso);
  }

  const allIsos = [...datedLane.map((d) => d.iso), ...datedOther];
  if (allIsos.length === 0) {
    return { buckets: [], granularity: "day", undatedCount, windowFrom: "", windowTo: "" };
  }

  const windowFrom = window?.from && /^\d{4}-\d{2}-\d{2}$/.test(window.from) ? window.from : allIsos.reduce((a, b) => (a < b ? a : b));
  const windowTo = window?.to && /^\d{4}-\d{2}-\d{2}$/.test(window.to) ? window.to : allIsos.reduce((a, b) => (a > b ? a : b));
  const spanDays = daysBetween(windowFrom, windowTo);
  const granularity = chooseGranularity(spanDays);
  const buckets = buildBuckets(granularity, windowFrom, windowTo);

  for (const b of buckets) {
    for (const { iso, laneKey } of datedLane) {
      if (!inRange(iso, b.from, b.to)) continue;
      b.total += 1;
      b.byLane[laneKey] = (b.byLane[laneKey] ?? 0) + 1;
    }
    for (const iso of datedOther) {
      if (inRange(iso, b.from, b.to)) b.total += 1;
    }
  }

  return { buckets, granularity, undatedCount, windowFrom, windowTo };
}

const entryIso = (e: { event_date?: string | null; available_at?: string | null }): string | null =>
  e.event_date ?? e.available_at ?? null;

export function entriesInBucket<T extends { event_date?: string | null; available_at?: string | null }>(
  entries: T[],
  bucket: Pick<ActivityBucket, "from" | "to">,
): T[] {
  return entries.filter((e) => {
    const iso = entryIso(e);
    return iso !== null && inRange(iso, bucket.from, bucket.to);
  });
}

const windowSpanDays = (windowFrom: string, windowTo: string): number =>
  Math.max(1, daysBetween(windowFrom, windowTo));

/** Proportional horizontal placement of a bucket inside the window (0–1 fractions). */
export function bucketSpanFraction(
  bucketFrom: string,
  bucketTo: string,
  windowFrom: string,
  windowTo: string,
): { left: number; width: number } {
  const span = windowSpanDays(windowFrom, windowTo);
  const start = daysBetween(windowFrom, bucketFrom) - 1;
  const widthDays = daysBetween(bucketFrom, bucketTo);
  const left = Math.max(0, Math.min(1, start / span));
  const width = Math.max(0, Math.min(1 - left, widthDays / span));
  return { left, width };
}

export function totalDatedInBuckets(buckets: ActivityBucket[]): number {
  return buckets.reduce((n, b) => n + b.total, 0);
}

/** Bucket with the highest total; ties keep the earliest bucket. */
export function busiestBucket(buckets: ActivityBucket[]): ActivityBucket | null {
  let best: ActivityBucket | null = null;
  for (const b of buckets) {
    if (b.total === 0) continue;
    if (!best || b.total > best.total) best = b;
  }
  return best;
}

/** True when every non-empty bucket has the same total. */
export function isActivityEvenlySpread(buckets: ActivityBucket[]): boolean {
  const totals = buckets.map((b) => b.total).filter((t) => t > 0);
  if (totals.length <= 1) return true;
  const first = totals[0]!;
  return totals.every((t) => t === first);
}

export type SparseAxisTick = { position: number; label: string };

/** Start, end, and up to two interior month markers for the density strip axis. */
export function sparseAxisTicks(windowFrom: string, windowTo: string, buckets: ActivityBucket[]): SparseAxisTick[] {
  const ticks: SparseAxisTick[] = [
    { position: 0, label: SHORT_DAY.format(new Date(`${windowFrom}T00:00:00Z`)) },
    { position: 1, label: SHORT_DAY.format(new Date(`${windowTo}T00:00:00Z`)) },
  ];
  if (buckets.length < 4) return ticks;
  const seen = new Set<string>();
  const interior: SparseAxisTick[] = [];
  for (const b of buckets) {
    const monthKey = b.from.slice(0, 7);
    if (seen.has(monthKey)) continue;
    seen.add(monthKey);
    const { left } = bucketSpanFraction(b.from, b.to, windowFrom, windowTo);
    if (left <= 0.08 || left >= 0.92) continue;
    interior.push({ position: left, label: SHORT_MONTH.format(new Date(`${b.from}T00:00:00Z`)) });
    if (interior.length >= 2) break;
  }
  return [...ticks.slice(0, 1), ...interior, ...ticks.slice(1)];
}
