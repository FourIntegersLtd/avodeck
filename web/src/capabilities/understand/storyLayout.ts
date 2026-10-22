// S11 story board layout. Drawing only: which card goes where. What counts as a thing to look at, a status or a
// link is the server's; here we group, rank and pack what it sent so nothing overlaps.
import type { Schemas } from "../../foundation/platform-client";
import { axisIso, statusOf } from "./shared";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];

export type GroupState = "look" | "please_check" | "checked";

/** One lane's entries on one day, led by the entry that matters most. */
export interface StoryGroup {
  key: string;
  laneKey: string;
  date: string;
  events: Event[];
  state: GroupState;
  questionIds: string[];
  /** 0 thing to look at, 1 linked to a question, 2 please check, 3 checked. Lower shows first. */
  rank: number;
}

export type Level = "years" | "months" | "days";

const DAY = 86_400_000;

export function dayMs(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
}

function eventRank(e: Event): number {
  if (e.has_finding) return 0;
  if (e.question_ids.length > 0) return 1;
  return statusOf(e.status) === "please_check" ? 2 : 3;
}

function stateOf(events: Event[]): GroupState {
  if (events.some((e) => e.has_finding)) return "look";
  return events.some((e) => statusOf(e.status) === "please_check") ? "please_check" : "checked";
}

/** Groups a lane's dated entries by day, in date order. Undated entries are listed elsewhere. */
export function groupByDay(laneKey: string, events: Event[]): StoryGroup[] {
  const byDay = new Map<string, Event[]>();
  for (const e of events) {
    const iso = axisIso(e);
    if (!iso) continue;
    const day = byDay.get(iso);
    if (day) day.push(e);
    else byDay.set(iso, [e]);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([date, list]) => {
      const sorted = [...list].sort((a, b) => eventRank(a) - eventRank(b) || a.page_no - b.page_no);
      const rank = Math.min(...sorted.map(eventRank));
      const questionIds = [...new Set(sorted.flatMap((e) => e.question_ids))];
      return { key: `${laneKey}|${date}`, laneKey, date, events: sorted, state: stateOf(sorted), questionIds, rank };
    });
}

export interface LayoutOptions {
  width: number;
  toX: (iso: string) => number;
  card: number;
  gap: number;
  rows: number;
}

export interface Placed {
  group: StoryGroup;
  x: number;
}

/** Places groups as cards, most important first, into the first row where they fit; the rest are folded. */
export function layoutLane(groups: StoryGroup[], o: LayoutOptions): { cards: (Placed & { row: number })[]; folded: Placed[] } {
  const taken: [number, number][][] = Array.from({ length: o.rows }, () => []);
  const cards: (Placed & { row: number })[] = [];
  const folded: Placed[] = [];
  const order = [...groups].sort((a, b) => a.rank - b.rank || (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  for (const group of order) {
    const at = o.toX(group.date);
    const x = Math.max(0, Math.min(at, o.width - o.card));
    const start = x - o.gap;
    const end = x + o.card + o.gap;
    const row = taken.findIndex((spans) => spans.every(([s, e]) => end <= s || start >= e));
    if (row === -1) {
      folded.push({ group, x: at });
      continue;
    }
    taken[row]!.push([x, x + o.card]);
    cards.push({ group, x, row });
  }
  cards.sort((a, b) => a.x - b.x || a.row - b.row);
  folded.sort((a, b) => a.x - b.x);
  return { cards, folded };
}

export interface FoldChip {
  x: number;
  count: number;
  groups: StoryGroup[];
}

/** Folded groups closer than `span` pixels share one counted chip (count of entries, not days). */
export function foldChips(folded: Placed[], span: number): FoldChip[] {
  const chips: FoldChip[] = [];
  let open: (FoldChip & { start: number }) | null = null;
  for (const f of [...folded].sort((a, b) => a.x - b.x)) {
    if (open && f.x - open.start < span) {
      open.groups.push(f.group);
      open.count += f.group.events.length;
      continue;
    }
    open = { start: f.x, x: f.x, count: f.group.events.length, groups: [f.group] };
    chips.push(open);
  }
  return chips.map(({ x, count, groups }) => ({ x, count, groups }));
}

/** How detailed the board is for a window: over three years, years; four months or less, days. */
export function levelFor(from: string, to: string): Level {
  const days = (dayMs(to) - dayMs(from)) / DAY;
  if (days > 3 * 365) return "years";
  if (days <= 122) return "days";
  return "months";
}

/** Each lane's dated entries counted per month, from the month of `from` to the month of `to`. */
export function monthlyCounts(lanes: Lane[], from: string, to: string): { months: string[]; byLane: Record<string, number[]> } {
  const months: string[] = [];
  let y = Number(from.slice(0, 4));
  let m = Number(from.slice(5, 7));
  const endKey = to.slice(0, 7);
  for (;;) {
    const key = `${y}-${String(m).padStart(2, "0")}`;
    months.push(key);
    if (key >= endKey) break;
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  const index = new Map(months.map((k, i) => [k, i]));
  const byLane: Record<string, number[]> = {};
  for (const lane of lanes) {
    const counts = new Array<number>(months.length).fill(0);
    for (const e of lane.events) {
      const i = index.get(axisIso(e)?.slice(0, 7) ?? "");
      if (i !== undefined) counts[i]! += 1;
    }
    byLane[lane.key] = counts;
  }
  return { months, byLane };
}

/** The first group, in any lane, on a later day than `group`. */
export function nextEntryAfter(all: StoryGroup[], group: StoryGroup): StoryGroup | null {
  let best: StoryGroup | null = null;
  for (const g of all) if (g.date > group.date && (!best || g.date < best.date)) best = g;
  return best;
}

export function daysBetween(from: string, to: string): number {
  return Math.round((dayMs(to) - dayMs(from)) / DAY);
}

/** Estimated width of one axis character: mono at 0.62em of the 17px axis text. */
export const AXIS_CHAR_PX = 0.62 * 17;
const AXIS_PAD = 16; // the label's own side padding
const AXIS_GAP = 12; // space kept clear after a label

const MONTH_LABEL = (year: boolean) => new Intl.DateTimeFormat("en-GB", { month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" });
const labelNeed = (label: string) => label.length * AXIS_CHAR_PX + AXIS_PAD + AXIS_GAP;

/**
 * Axis labels for a window, never closer together than the earlier label is wide. Months step on calendar months
 * (quarters are Jan, Apr, Jul, Oct); January always has a place and wins over a neighbour that is not January.
 */
export function axisMarks(start: number, end: number, level: Level, track: number): { at: number; label: string }[] {
  const span = Math.max(end - start, DAY);
  const total = Math.round((end - start) / (30.44 * DAY));
  const step = level === "years" ? 12 : total > 12 ? 3 : 1;
  const candidates: { at: number; january: boolean; label: string }[] = [];
  for (let d = new Date(start), i = 0; d.getTime() < end; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)), i++) {
    const january = d.getUTCMonth() === 0;
    if (level === "years" ? !(january || i === 0) : d.getUTCMonth() % step !== 0) continue;
    const label = level === "years" ? String(d.getUTCFullYear()) : MONTH_LABEL(january).format(d);
    candidates.push({ at: d.getTime(), january, label });
  }
  const x = (at: number) => ((at - start) / span) * track;
  const kept: typeof candidates = [];
  for (const c of candidates) {
    let prev = kept.at(-1);
    if (prev && x(c.at) - x(prev.at) < labelNeed(prev.label) && c.january && !prev.january) {
      kept.pop();
      prev = kept.at(-1);
    }
    if (prev && x(c.at) - x(prev.at) < labelNeed(prev.label)) continue;
    kept.push(c);
  }
  // The first mark names its year when it is not January; a January that follows would have replaced it.
  const first = kept[0];
  if (first && level !== "years" && !first.january) first.label = MONTH_LABEL(true).format(new Date(first.at));
  return kept.map(({ at, label }) => ({ at, label }));
}
