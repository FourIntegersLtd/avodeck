// S11 series: entries the lane's pack rule groups into one bar (e.g. one medicine issued again and again).
// Bars sit in up to three rows above the lane's cards; each issue shows as a tick at its own date. Entries that are
// a thing to look at, or linked to a question, keep their own card as well.
import type { Schemas } from "../../foundation/platform-client";
import { ROW } from "./boardGeometry";
import { seriesIssues } from "./series.words";
import { longDate } from "./shared";
import "./series.css";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];
type Bar = NonNullable<Lane["series"]>[number];

const MAX_ROWS = 3;
const LABEL_MARGIN_DAYS = 30;
const MIN_WIDTH = 48;
const DAY = 86_400_000;

/** The bars that fit, each with its row: packed greedily by time (dates, not pixels), at most three rows. */
function place(lane: Lane): { bar: Bar; row: number }[] {
  const ends: number[] = [];
  const placed: { bar: Bar; row: number }[] = [];
  for (const bar of [...(lane.series ?? [])].sort((a, b) => a.first.localeCompare(b.first))) {
    const start = Date.parse(bar.first);
    let row = ends.findIndex((end) => end < start);
    if (row < 0) {
      if (ends.length >= MAX_ROWS) continue;
      row = ends.length;
      ends.push(0);
    }
    ends[row] = Date.parse(bar.last) + LABEL_MARGIN_DAYS * DAY;
    placed.push({ bar, row });
  }
  return placed;
}

/** How many card rows the lane's bars take, above its cards. */
export function seriesRows(lane: Lane): number {
  return place(lane).reduce((n, p) => Math.max(n, p.row + 1), 0);
}

/** Entries drawn on a bar instead of a card. */
export function seriesFacts(lane: Lane): Set<string> {
  const own = new Map(lane.events.map((e) => [e.fact_id, e]));
  const out = new Set<string>();
  for (const { bar } of place(lane)) {
    for (const id of bar.fact_ids) {
      const e = own.get(id);
      if (e && !e.has_finding && !e.question_ids.length) out.add(id);
    }
  }
  return out;
}

export function SeriesBars({ lane, toX, onOpen }: { lane: Lane; toX: (iso: string) => number; track: number; onOpen: (events: Event[], at?: string) => void }) {
  const own = new Map(lane.events.map((e) => [e.fact_id, e]));
  return (
    <>
      {place(lane).map(({ bar, row }) => {
        const left = toX(bar.first);
        const width = Math.max(toX(bar.last) - left, MIN_WIDTH);
        const events = bar.fact_ids.map((id) => own.get(id)).filter((e): e is Event => !!e);
        return (
          <button
            key={bar.key}
            type="button"
            className="av-series-bar"
            style={{ left, width, top: row * ROW + 4 }}
            data-facts={bar.fact_ids.join(" ")}
            onClick={() => onOpen(events, bar.fact_ids[0])}
          >
            <span className="av-series-bar__label">{bar.label}</span>
            <span className="av-visually-hidden"> · {seriesIssues(bar.count, longDate(bar.first), longDate(bar.last))}</span>
            <span className="av-series-bar__band" aria-hidden="true" style={{ background: lane.colour ?? "var(--ink-soft)" }}>
              {events.map((e) => (e.event_date ? <span key={e.fact_id} className="av-series-bar__tick" style={{ left: toX(e.event_date) - left }} /> : null))}
            </span>
          </button>
        );
      })}
    </>
  );
}
