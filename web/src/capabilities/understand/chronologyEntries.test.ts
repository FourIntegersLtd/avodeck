import { describe, expect, test } from "vitest";
import { chronologyEntries } from "./chronologyEntries";
import { event } from "./testing";

describe("chronologyEntries", () => {
  test("sorts dated events across lanes, undated last", () => {
    const lanes = [
      { key: "lane_0", label: "A", events: [event({ fact_id: "b", event_date: "2024-03-01", page_no: 2 })] },
      { key: "lane_1", label: "B", events: [event({ fact_id: "a", event_date: "2024-02-01", page_no: 1 })] },
    ];
    const unplaced = [event({ fact_id: "c", event_date: null, available_at: null, page_no: 9 })];
    const rows = chronologyEntries(lanes, unplaced);
    expect(rows.map((r) => r.fact_id)).toEqual(["a", "b", "c"]);
    expect(rows[0]!.laneLabel).toBe("B");
  });
});
