import { describe, expect, test } from "vitest";
import { instructionPeriod, lastTwoYearsWindow, periodDefaultDates } from "./timelineTimeWindow";

describe("timelineTimeWindow", () => {
  test("instruction period wins when the case has both ends", () => {
    expect(instructionPeriod({ period_start: "2024-02-01", period_end: "2024-08-31" })).toEqual({ from: "2024-02-01", to: "2024-08-31" });
    expect(periodDefaultDates({ period_start: "2024-02-01", period_end: "2024-08-31" }, { first: "1990-01-01", last: "2025-01-01" })).toEqual({
      from: "2024-02-01",
      to: "2024-08-31",
    });
  });

  test("without instruction period, defaults to the last two years of the record", () => {
    expect(periodDefaultDates({ period_start: null, period_end: null }, { first: "2018-06-01", last: "2024-05-15" })).toEqual({
      from: "2022-05-15",
      to: "2024-05-15",
    });
    expect(lastTwoYearsWindow({ first: "2023-01-01", last: "2024-01-01" })).toEqual({ from: "2023-01-01", to: "2024-01-01" });
  });
});
