// S11 series: entries the lane's pack rule groups into one bar (e.g. one medicine issued again and again).
// The series stream (docs/design/timeline-v2-followups.md, item 5) draws them; until then a lane has none.
import type { Schemas } from "../../foundation/platform-client";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];

/** How many card rows the lane's bars take, above its cards. */
export function seriesRows(lane: Lane): number {
  return lane.series?.length ? 0 : 0;
}

/** Entries drawn on a bar instead of a card. */
export function seriesFacts(lane: Lane): Set<string> {
  void lane;
  return new Set();
}

export function SeriesBars(props: { lane: Lane; toX: (iso: string) => number; track: number; onOpen: (events: Event[], at?: string) => void }) {
  void props;
  return null;
}
