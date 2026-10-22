// S11 story board layout: entries are grouped by lane and day, ranked by what matters most, packed into a few
// rows without overlapping, and whatever does not fit is folded into counted marks. Pure functions, no browser.
import { describe, expect, test } from "vitest";
import { foldChips, groupByDay, layoutLane, levelFor, monthlyCounts, nextEntryAfter, type StoryGroup } from "./storyLayout";
import { event, Q_1 } from "./testing";

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

describe("groupByDay", () => {
  test("entries on the same day become one group carrying the strongest status and every question", () => {
    const groups = groupByDay("results", [
      event({ fact_id: id(1), event_date: "2024-02-19", status: "verified" }),
      event({ fact_id: id(2), event_date: "2024-02-19", status: "suggested", question_ids: [Q_1] }),
      event({ fact_id: id(3), event_date: "2024-02-19", status: "verified", has_finding: true }),
      event({ fact_id: id(4), event_date: "2024-03-01", status: "verified" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0]!.events).toHaveLength(3);
    expect(groups[0]!.state).toBe("look");
    expect(groups[0]!.questionIds).toEqual([Q_1]);
    expect(groups[0]!.laneKey).toBe("results");
    expect(groups[1]!.state).toBe("checked");
  });

  test("the entry that matters most leads its group, and undated entries are left out", () => {
    const groups = groupByDay("lane", [
      event({ fact_id: id(1), event_date: "2024-02-19", summary: "Plain" }),
      event({ fact_id: id(2), event_date: "2024-02-19", summary: "Flagged", has_finding: true }),
      event({ fact_id: id(3), event_date: null, available_at: null }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.events[0]!.summary).toBe("Flagged");
  });

  test("a group's rank puts things to look at first, then question links, then please check, then checked", () => {
    const [look, linked, check, done] = [
      groupByDay("l", [event({ has_finding: true })])[0]!,
      groupByDay("l", [event({ question_ids: [Q_1] })])[0]!,
      groupByDay("l", [event({ status: "suggested" })])[0]!,
      groupByDay("l", [event()])[0]!,
    ];
    expect([look.rank, linked.rank, check.rank, done.rank]).toEqual([0, 1, 2, 3]);
  });
});

describe("layoutLane", () => {
  const days = (n: number) => Array.from({ length: n }, (_, i) => `2024-01-${String(i + 1).padStart(2, "0")}`);
  const toX = (iso: string) => (Number(iso.slice(8, 10)) - 1) * 10;
  const groupsOn = (dates: string[], over: (i: number) => Parameters<typeof event>[0] = () => ({})) =>
    dates.flatMap((d, i) => groupByDay("l", [event({ fact_id: id(i), event_date: d, ...over(i) })]));

  test("cards never overlap within a row", () => {
    const out = layoutLane(groupsOn(days(30)), { width: 600, toX, card: 150, gap: 12, rows: 3 });
    for (const a of out.cards)
      for (const b of out.cards) {
        if (a === b || a.row !== b.row) continue;
        expect(a.x + 150 + 12 <= b.x || b.x + 150 + 12 <= a.x).toBe(true);
      }
    expect(Math.max(...out.cards.map((c) => c.row))).toBeLessThan(3);
  });

  test("everything that does not fit is folded, so cards and folded add up to the groups", () => {
    const groups = groupsOn(days(30));
    const out = layoutLane(groups, { width: 600, toX, card: 150, gap: 12, rows: 2 });
    expect(out.cards.length + out.folded.length).toBe(groups.length);
    expect(out.folded.length).toBeGreaterThan(0);
  });

  test("when space is short, the entry that matters most gets the card", () => {
    const groups = groupsOn(days(3), (i) => (i === 2 ? { has_finding: true } : {}));
    const out = layoutLane(groups, { width: 600, toX, card: 150, gap: 12, rows: 1 });
    expect(out.cards).toHaveLength(1);
    expect(out.cards[0]!.group.state).toBe("look");
  });

  test("a card near the right edge is kept inside the track", () => {
    const out = layoutLane(groupsOn(["2024-01-30"]), { width: 300, toX, card: 150, gap: 12, rows: 1 });
    expect(out.cards[0]!.x).toBeLessThanOrEqual(150);
  });
});

describe("foldChips", () => {
  test("folded entries near each other are counted into one chip", () => {
    const g = (n: number): StoryGroup => groupByDay("l", Array.from({ length: n }, (_, i) => event({ fact_id: id(100 + i) })))[0]!;
    const chips = foldChips(
      [
        { group: g(2), x: 10 },
        { group: g(3), x: 30 },
        { group: g(1), x: 400 },
      ],
      64,
    );
    expect(chips.map((c) => c.count)).toEqual([5, 1]);
    expect(chips[0]!.groups).toHaveLength(2);
  });
});

describe("levelFor", () => {
  test("a long window shows years, a middling one months, a short one days", () => {
    expect(levelFor("2004-01-01", "2024-12-31")).toBe("years");
    expect(levelFor("2024-02-01", "2024-08-31")).toBe("months");
    expect(levelFor("2024-05-01", "2024-06-15")).toBe("days");
  });
});

describe("monthlyCounts", () => {
  test("counts each lane's dated entries per month across the span", () => {
    const counts = monthlyCounts(
      [
        { key: "a", label: "A", events: [event({ event_date: "2024-01-05" }), event({ event_date: "2024-01-20" }), event({ event_date: "2024-03-02" })] },
        { key: "b", label: "B", events: [event({ event_date: "2024-02-10" }), event({ event_date: null, available_at: null })] },
      ],
      "2024-01-01",
      "2024-03-31",
    );
    expect(counts.months).toEqual(["2024-01", "2024-02", "2024-03"]);
    expect(counts.byLane.a).toEqual([2, 0, 1]);
    expect(counts.byLane.b).toEqual([0, 1, 0]);
  });
});

describe("nextEntryAfter", () => {
  test("finds the next later day in any lane", () => {
    const all = [...groupByDay("a", [event({ event_date: "2024-02-19" })]), ...groupByDay("b", [event({ event_date: "2024-02-19" }), event({ fact_id: id(9), event_date: "2024-05-15" })])];
    expect(nextEntryAfter(all, all[0]!)?.date).toBe("2024-05-15");
    expect(nextEntryAfter(all, all.at(-1)!)).toBeNull();
  });
});
