import { describe, expect, test } from "vitest";
import { clampSplit, DEFAULT_SPLIT_PCT, MAX_SPLIT_PCT, MIN_SPLIT_PCT } from "./splitPane";

describe("reader split pane", () => {
  test("clamps to a safe range", () => {
    expect(clampSplit(10)).toBe(MIN_SPLIT_PCT);
    expect(clampSplit(90)).toBe(MAX_SPLIT_PCT);
    expect(clampSplit(55)).toBe(55);
    expect(clampSplit(Number.NaN)).toBe(DEFAULT_SPLIT_PCT);
  });
});
