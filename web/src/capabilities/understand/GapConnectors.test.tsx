// S11 gaps: the dashed line from an entry an open thing to look at rests on to the entry that followed, drawn only
// from the server's `gaps` and worded by the server's label key. No local guess of how long is "long".
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CARD } from "./boardGeometry";
import { GapConnectors } from "./GapConnectors";
import { dayMs, groupByDay } from "./storyLayout";
import { event, FACT_1, FACT_2, FACT_3 } from "./testing";

const START = dayMs("2024-01-01");
const toX = (iso: string) => ((dayMs(iso) - START) / 86_400_000) * 4;
const FINDING = "c1111111-1111-4111-8111-111111111111";

const from = groupByDay("lane_one", [event({ fact_id: FACT_2, event_date: "2024-01-10", has_finding: true })])[0]!;
const to = groupByDay("lane_two", [event({ fact_id: FACT_1, event_date: "2024-03-10" })])[0]!;
const last = groupByDay("lane_two", [event({ fact_id: FACT_3, event_date: "2024-05-01" })])[0]!;
const cards = [{ group: from, x: toX(from.date), row: 0 }];

function gap(over: Partial<Parameters<typeof GapConnectors>[0]["gaps"][number]> = {}) {
  return { finding_id: FINDING, from_fact_id: FACT_2, to_fact_id: FACT_1, days: 60, kind: "no_action_within", label_key: "timeline.gap.no_action_within", ...over };
}

function draw(gaps: Parameters<typeof GapConnectors>[0]["gaps"]) {
  return render(<GapConnectors cards={cards} all={[from, to, last]} gaps={gaps} toX={toX} rowOffset={0} />);
}

describe("GapConnectors", () => {
  test("draws the server's words for the wait", () => {
    draw([gap()]);
    expect(screen.getByText("60 days. Nothing in the record shows what followed.")).toBeInTheDocument();
  });

  test("runs from the right edge of the card to the entry that followed", () => {
    const { container } = draw([gap()]);
    const line = container.querySelector(".av-story-gap") as HTMLElement;
    expect(line.style.left).toBe(`${toX(from.date) + CARD}px`);
    expect(line.style.width).toBe(`${toX(to.date) - (toX(from.date) + CARD)}px`);
  });

  test("a wait with nothing after it runs to the latest date and says so", () => {
    const { container } = draw([gap({ to_fact_id: null, days: 112, kind: "no_action_within", label_key: "timeline.gap.none_after" })]);
    expect(screen.getByText("Nothing in the record after this.")).toBeInTheDocument();
    const line = container.querySelector(".av-story-gap") as HTMLElement;
    expect(line.style.width).toBe(`${toX(last.date) - (toX(from.date) + CARD)}px`);
  });

  test("the other kinds of wait have their own words, and one day is singular", () => {
    draw([gap({ label_key: "timeline.gap.records_quiet", days: 1 })]);
    expect(screen.getByText("1 day. The records go quiet here, so pages may be missing.")).toBeInTheDocument();
  });

  test("a rule with no words of its own falls back to the plain count", () => {
    draw([gap({ label_key: "timeline.gap.zz_unknown" })]);
    expect(screen.getByText("60 days to the next entry")).toBeInTheDocument();
  });

  test("draws nothing without gaps, and nothing for a gap whose entry is not on this lane", () => {
    const { container, rerender } = draw([]);
    expect(container.querySelector(".av-story-gap")).toBeNull();
    rerender(<GapConnectors cards={cards} all={[from, to]} gaps={[gap({ from_fact_id: FACT_3 })]} toX={toX} rowOffset={0} />);
    expect(container.querySelector(".av-story-gap")).toBeNull();
  });
});
