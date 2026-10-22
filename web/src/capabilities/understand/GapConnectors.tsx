// S11 gaps: the dashed line from a card an open thing to look at rests on, with how long the wait was.
// The gaps stream (docs/design/timeline-v2-followups.md, item 4) draws these from the server's `gaps`.
import type { Schemas } from "../../foundation/platform-client";
import { CARD, ROW } from "./boardGeometry";
import { daysBetween, nextEntryAfter, type StoryGroup } from "./storyLayout";
import { w } from "./words";

type Gap = Schemas["Gap"];

/** Shorter waits than this are ordinary, so no gap is drawn for them. */
const GAP_DAYS = 14;

export function GapConnectors({
  cards,
  all,
  toX,
  rowOffset,
}: {
  cards: { group: StoryGroup; x: number; row: number }[];
  all: StoryGroup[];
  gaps: Gap[];
  toX: (iso: string) => number;
  rowOffset: number;
}) {
  const drawn = cards
    .filter((c) => c.group.state === "look")
    .map((c) => {
      const next = nextEntryAfter(all, c.group);
      if (!next || daysBetween(c.group.date, next.date) < GAP_DAYS) return null;
      const from = c.x + CARD;
      return { key: c.group.key, left: from, width: Math.max(0, toX(next.date) - from), top: (rowOffset + c.row) * ROW + 2, days: daysBetween(c.group.date, next.date) };
    })
    .filter((g): g is NonNullable<typeof g> => g !== null);
  return (
    <>
      {drawn.map((g) => (
        <span key={g.key} className="av-story-gap" style={{ left: g.left, width: g.width, top: g.top }}>
          <span>{w("timeline.gap", { count: g.days })}</span>
        </span>
      ))}
    </>
  );
}
