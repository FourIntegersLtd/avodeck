// S11 gaps: the dashed line from a card an open thing to look at rests on, with how long the wait was.
// Everything comes from the server's `gaps` (which entry, how long, in which words); nothing is guessed here.
import type { Schemas } from "../../foundation/platform-client";
import { CARD, ROW } from "./boardGeometry";
import { gapWords } from "./gaps.words";
import "./gaps.css";
import type { StoryGroup } from "./storyLayout";

type Gap = Schemas["Gap"];

export function GapConnectors({
  cards,
  all,
  gaps,
  toX,
  rowOffset,
}: {
  cards: { group: StoryGroup; x: number; row: number }[];
  all: StoryGroup[];
  gaps: Gap[];
  toX: (iso: string) => number;
  rowOffset: number;
}) {
  const latest = all.reduce<string | null>((best, g) => (best === null || g.date > best ? g.date : best), null);
  const drawn = gaps.flatMap((gap) => {
    const card = cards.find((c) => c.group.events.some((e) => e.fact_id === gap.from_fact_id));
    if (!card) return [];
    const target = gap.to_fact_id ? all.find((g) => g.events.some((e) => e.fact_id === gap.to_fact_id)) : undefined;
    const end = target?.date ?? latest;
    if (end === null) return [];
    const left = card.x + CARD;
    return [{ key: gap.finding_id, left, width: Math.max(0, toX(end) - left), top: (rowOffset + card.row) * ROW + 2, text: gapWords(gap.label_key, gap.days) }];
  });
  return (
    <>
      {drawn.map((g) => (
        <span key={g.key} className="av-story-gap" style={{ left: g.left, width: g.width, top: g.top }}>
          <span>{g.text}</span>
        </span>
      ))}
    </>
  );
}
