import { describe, expect, test } from "vitest";
import { labelledDotIds, thinMonthMarks } from "./timelineLayout";

describe("labelledDotIds", () => {
  test("labels only one dot when two sit too close", () => {
    const ids = labelledDotIds([
      { id: "a", left: 10 },
      { id: "b", left: 15 },
    ]);
    expect(ids.size).toBe(1);
    expect(ids.has("a")).toBe(true);
  });

  test("labels both when far enough apart", () => {
    const ids = labelledDotIds([
      { id: "a", left: 10 },
      { id: "b", left: 30 },
    ]);
    expect(ids).toEqual(new Set(["a", "b"]));
  });

  test("shows no track labels when more than four entries", () => {
    const ids = labelledDotIds([
      { id: "a", left: 5 },
      { id: "b", left: 25 },
      { id: "c", left: 45 },
      { id: "d", left: 65 },
      { id: "e", left: 85 },
    ]);
    expect(ids.size).toBe(0);
  });
});

describe("thinMonthMarks", () => {
  const marks = Array.from({ length: 15 }, (_, i) => ({ at: Date.UTC(2024, i, 1), label: `M${i}` }));

  test("keeps every month in month zoom", () => {
    expect(thinMonthMarks(marks, "month")).toHaveLength(15);
  });

  test("thins a long whole-record span", () => {
    expect(thinMonthMarks(marks, "whole").length).toBeLessThan(marks.length);
  });
});
