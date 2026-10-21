import { describe, expect, test } from "vitest";
import {
  activityBuckets,
  bucketSpanFraction,
  busiestBucket,
  isActivityEvenlySpread,
  sparseAxisTicks,
  totalDatedInBuckets,
} from "./activityBuckets";
import { event } from "./testing";

describe("activityBuckets", () => {
  test("returns no buckets when there are no dated events", () => {
    const lanes = [{ key: "lane_a", label: "A", events: [event({ event_date: null, available_at: null })] }];
    const out = activityBuckets(lanes, []);
    expect(out.buckets).toEqual([]);
    expect(out.undatedCount).toBe(1);
  });

  test("uses day buckets for a short window and sums lane counts to total", () => {
    const lanes = [
      {
        key: "lane_a",
        label: "A",
        events: [event({ fact_id: "a1", event_date: "2024-05-10" }), event({ fact_id: "a2", event_date: "2024-05-12" })],
      },
      { key: "lane_b", label: "B", events: [event({ fact_id: "b1", event_date: "2024-05-11" })] },
    ];
    const out = activityBuckets(lanes, [], { from: "2024-05-10", to: "2024-05-12" });
    expect(out.granularity).toBe("day");
    expect(out.buckets).toHaveLength(3);
    expect(out.buckets[1]!.total).toBe(1);
    expect(out.buckets[1]!.byLane.lane_b).toBe(1);
    const sum = out.buckets.reduce((n, b) => n + b.total, 0);
    expect(sum).toBe(3);
  });

  test("uses week buckets when the window is longer than sixty days", () => {
    const lanes = [{ key: "lane_a", label: "A", events: [event({ event_date: "2024-01-15" }), event({ event_date: "2024-03-01" })] }];
    const out = activityBuckets(lanes, [], { from: "2024-01-01", to: "2024-03-31" });
    expect(out.granularity).toBe("week");
    expect(out.buckets.length).toBeGreaterThan(4);
    expect(out.buckets.some((b) => b.from <= "2024-01-15" && b.to >= "2024-01-15")).toBe(true);
  });

  test("uses month buckets when the window is longer than two years", () => {
    const lanes = [{ key: "lane_a", label: "A", events: [event({ event_date: "2020-06-01" }), event({ event_date: "2023-01-01" })] }];
    const out = activityBuckets(lanes, [], { from: "2020-01-01", to: "2023-12-31" });
    expect(out.granularity).toBe("month");
    expect(out.buckets[0]!.from).toBe("2020-01-01");
    expect(out.buckets.at(-1)!.to).toBe("2023-12-31");
  });

  test("counts dated unplaced events in totals but not in lane stacks", () => {
    const lanes = [{ key: "lane_a", label: "A", events: [event({ fact_id: "a1", event_date: "2024-05-15" })] }];
    const unplaced = [event({ fact_id: "u1", event_date: "2024-05-15", page_no: 9 })];
    const out = activityBuckets(lanes, unplaced, { from: "2024-05-15", to: "2024-05-15" });
    expect(out.buckets[0]!.total).toBe(2);
    expect(out.buckets[0]!.byLane.lane_a).toBe(1);
  });
});

describe("bucketSpanFraction", () => {
  test("maps a single day to full width in a one-day window", () => {
    expect(bucketSpanFraction("2024-05-10", "2024-05-10", "2024-05-10", "2024-05-10")).toEqual({ left: 0, width: 1 });
  });

  test("places the middle day in the centre third of a three-day window", () => {
    const mid = bucketSpanFraction("2024-05-11", "2024-05-11", "2024-05-10", "2024-05-12");
    expect(mid.left).toBeCloseTo(1 / 3, 5);
    expect(mid.width).toBeCloseTo(1 / 3, 5);
  });
});

describe("activity summaries", () => {
  test("totalDatedInBuckets sums bucket totals", () => {
    const buckets = [
      { key: "a", label: "a", from: "2024-01-01", to: "2024-01-01", total: 2, byLane: {} },
      { key: "b", label: "b", from: "2024-01-02", to: "2024-01-02", total: 1, byLane: {} },
    ];
    expect(totalDatedInBuckets(buckets)).toBe(3);
  });

  test("busiestBucket picks the highest total", () => {
    const buckets = [
      { key: "a", label: "Jan", from: "2024-01-01", to: "2024-01-31", total: 1, byLane: {} },
      { key: "b", label: "Feb", from: "2024-02-01", to: "2024-02-29", total: 5, byLane: {} },
    ];
    expect(busiestBucket(buckets)?.key).toBe("b");
  });

  test("isActivityEvenlySpread is true when active buckets match", () => {
    const even = [
      { key: "a", label: "a", from: "2024-01-01", to: "2024-01-01", total: 2, byLane: {} },
      { key: "b", label: "b", from: "2024-01-02", to: "2024-01-02", total: 2, byLane: {} },
    ];
    expect(isActivityEvenlySpread(even)).toBe(true);
    const uneven = [
      { key: "a", label: "a", from: "2024-01-01", to: "2024-01-01", total: 2, byLane: {} },
      { key: "b", label: "b", from: "2024-01-02", to: "2024-01-02", total: 5, byLane: {} },
    ];
    expect(isActivityEvenlySpread(uneven)).toBe(false);
  });

  test("sparseAxisTicks includes start and end", () => {
    const buckets = [
      { key: "a", label: "10 May", from: "2024-05-10", to: "2024-05-10", total: 1, byLane: {} },
      { key: "b", label: "11 May", from: "2024-05-11", to: "2024-05-11", total: 0, byLane: {} },
      { key: "c", label: "12 May", from: "2024-05-12", to: "2024-05-12", total: 1, byLane: {} },
    ];
    const ticks = sparseAxisTicks("2024-05-10", "2024-05-12", buckets);
    expect(ticks[0]!.position).toBe(0);
    expect(ticks.at(-1)!.position).toBe(1);
  });
});
