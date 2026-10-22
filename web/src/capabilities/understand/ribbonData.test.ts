// The ribbon draws from one shape (the overview: months, counts per lane, pins), whichever request filled it.
import { describe, expect, test } from "vitest";
import { ribbonFromTimeline } from "./ribbonData";
import { event, FACT_2, Q_1, timeline } from "./testing";

describe("ribbonFromTimeline", () => {
  test("counts each lane's entries per month and pins things to look at and question links", () => {
    const body = timeline({
      lanes: [
        { key: "a", label: "A", colour: "#111111", events: [event({ event_date: "2024-01-05" }), event({ event_date: "2024-03-02", question_ids: [Q_1] })] },
        { key: "b", label: "B", events: [event({ fact_id: FACT_2, event_date: "2024-02-10", has_finding: true })] },
      ],
      range: { first: "2024-01-05", last: "2024-03-02" },
    });
    const r = ribbonFromTimeline(body);
    expect(r.months).toEqual(["2024-01", "2024-02", "2024-03"]);
    expect(r.lanes).toEqual([
      { key: "a", label: "A", colour: "#111111", counts: [1, 0, 1] },
      { key: "b", label: "B", colour: null, counts: [0, 1, 0] },
    ]);
    expect(r.pins.map((p) => [p.lane_key, p.date, p.has_finding])).toEqual([
      ["b", "2024-02-10", true],
      ["a", "2024-03-02", false],
    ]);
    expect(r.range).toEqual({ first: "2024-01-05", last: "2024-03-02" });
  });

  test("a record with no dates gives an empty ribbon", () => {
    const r = ribbonFromTimeline(timeline({ lanes: [{ key: "a", label: "A", events: [] }], range: null }));
    expect(r).toEqual({ range: null, months: [], lanes: [{ key: "a", label: "A", colour: null, counts: [] }], pins: [] });
  });
});
