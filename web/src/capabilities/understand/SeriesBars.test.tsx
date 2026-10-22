// S11 series bars: entries a lane's pack rule groups (e.g. one medicine issued again and again) draw as one bar.
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { CASE_A } from "../../test/fixtures";
import { ROW } from "./boardGeometry";
import { SeriesBars, seriesFacts, seriesRows } from "./SeriesBars";
import { TimelineBoard } from "./TimelineBoard";
import { event, Q_1 } from "./testing";

type Lane = Schemas["Timeline"]["lanes"][number];

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const day = (offset: number) => new Date(Date.UTC(2024, 0, 1 + offset)).toISOString().slice(0, 10);
const issue = (n: number, offset: number, over: Partial<Schemas["Event"]> = {}) => event({ fact_id: id(n), page_no: n, event_date: day(offset), available_at: day(offset), ...over });

/** A bar over `issues` (offset in days from 1 Jan 2024). */
function bar(key: string, label: string, offsets: number[], first = 1) {
  const ids = offsets.map((_, i) => id(first + i));
  return { key, label, first: day(offsets[0]!), last: day(offsets[offsets.length - 1]!), count: offsets.length, fact_ids: ids };
}
function laneWith(bars: ReturnType<typeof bar>[], events: Schemas["Event"][], over: Partial<Lane> = {}): Lane {
  return { key: "lane_s", label: "Lane s", events, colour: "#2a6f97", series: bars, ...over };
}
function simple(): Lane {
  const events = [0, 30, 60].map((o, i) => issue(i + 1, o, { series_key: "k1" }));
  return laneWith([bar("k1", "Words one 210 mg", [0, 30, 60])], events);
}
const toX = (iso: string) => (Date.parse(iso) - Date.parse("2024-01-01")) / 86_400_000 * 3;

describe("seriesRows", () => {
  test("is 0 for a lane with no series", () => {
    expect(seriesRows({ key: "a", label: "A", events: [issue(1, 0)] })).toBe(0);
    expect(seriesRows({ key: "a", label: "A", events: [], series: [] })).toBe(0);
  });
  test("one row when bars do not overlap in time, two when they do", () => {
    const apart = laneWith([bar("a", "A", [0, 20], 1), bar("b", "B", [200, 220], 3)], []);
    expect(seriesRows(apart)).toBe(1);
    const together = laneWith([bar("a", "A", [0, 40], 1), bar("b", "B", [10, 50], 3)], []);
    expect(seriesRows(together)).toBe(2);
  });
  test("is capped at three rows", () => {
    const many = laneWith([0, 1, 2, 3, 4].map((n) => bar(`k${n}`, `K${n}`, [n, 50 + n], n * 2 + 1)), []);
    expect(seriesRows(many)).toBe(3);
  });
});

describe("seriesFacts", () => {
  test("holds the issues on drawn bars, minus those flagged or linked to a question", () => {
    const events = [issue(1, 0, { series_key: "k1" }), issue(2, 30, { series_key: "k1", has_finding: true }), issue(3, 60, { series_key: "k1", question_ids: [Q_1] }), issue(4, 90, { series_key: "k1" })];
    const lane = laneWith([bar("k1", "Words", [0, 30, 60, 90])], events);
    expect([...seriesFacts(lane)].sort()).toEqual([id(1), id(4)]);
  });
  test("leaves out bars past the three-row cap, and is empty with no series", () => {
    const bars = [0, 1, 2, 3].map((n) => bar(`k${n}`, `K${n}`, [n, 50 + n], n * 2 + 1));
    const events = bars.flatMap((b) => b.fact_ids.map((f, i) => event({ fact_id: f, event_date: b.first, page_no: i + 1 })));
    expect(seriesFacts(laneWith(bars, events)).size).toBe(6);
    expect(seriesFacts({ key: "a", label: "A", events: [issue(1, 0)] }).size).toBe(0);
  });
});

describe("SeriesBars", () => {
  test("a bar is a button named by its words and its issues, and opens every issue", () => {
    const lane = simple();
    const onOpen = vi.fn();
    render(<SeriesBars lane={lane} toX={toX} track={600} onOpen={onOpen} />);
    const button = screen.getByRole("button", { name: /^Words one 210 mg/ });
    expect(button).toHaveAccessibleName(/3 issues from 1 January 2024 to 1 March 2024/);
    expect(button.getBoundingClientRect).toBeDefined();
    expect(button.style.left).toBe(`${toX("2024-01-01")}px`);
    expect(button.style.width).toBe(`${toX("2024-03-01")}px`);
    expect(button.style.top).toBe("4px");
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0]![0].map((e: Schemas["Event"]) => e.fact_id)).toEqual([id(1), id(2), id(3)]);
    expect(onOpen.mock.calls[0]![1]).toBe(id(1));
  });
  test("a narrow bar is at least 48px wide, and a second row sits one row lower", () => {
    const lane = laneWith([bar("a", "A", [0, 40], 1), bar("b", "B", [1, 2], 3)], [0, 40, 1, 2].map((o, i) => issue(i + 1, o)));
    render(<SeriesBars lane={lane} toX={toX} track={600} onOpen={() => undefined} />);
    expect(screen.getByRole("button", { name: /^B/ }).style.width).toBe("48px");
    expect(screen.getByRole("button", { name: /^B/ }).style.top).toBe(`${ROW + 4}px`);
  });
  test("draws nothing for a lane without series", () => {
    const { container } = render(<SeriesBars lane={{ key: "a", label: "A", events: [issue(1, 0)] }} toX={toX} track={600} onOpen={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("on the board", () => {
  test("issues on a bar get no card, a flagged one keeps its card, and cards sit below the bar rows", () => {
    const events = [issue(1, 0, { series_key: "k1" }), issue(2, 30, { series_key: "k1", has_finding: true }), issue(3, 60, { series_key: "k1" })];
    const lane = laneWith([bar("k1", "Words one 210 mg", [0, 30, 60])], events);
    const { container } = render(
      <MemoryRouter>
        <TimelineBoard caseId={CASE_A} data={{ lanes: [lane], unplaced: [] }} from="2024-01-01" to="2024-04-01" onOpen={() => undefined} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /^Words one 210 mg/ })).toBeInTheDocument();
    const shown = [...container.querySelectorAll<HTMLElement>("[data-facts]")].flatMap((c) => c.dataset.facts!.split(" "));
    expect(shown).toEqual([id(2)]);
    for (const card of container.querySelectorAll<HTMLElement>(".av-story-card")) {
      expect(parseFloat(card.style.top)).toBeGreaterThanOrEqual(ROW * seriesRows(lane));
    }
  });
});
