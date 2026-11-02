import { describe, expect, test } from "vitest";
import { setSessionLocale, addDays, addMonths, addYears, daysBetween, formatDay, formatList, formatNumber, parseDay } from "./dates";

describe("parseDay", () => {
  test("a calendar day is a fixed UTC moment, a moment is itself", () => {
    expect(parseDay("2024-02-29").toISOString()).toBe("2024-02-29T12:00:00.000Z");
    expect(parseDay("2024-02-29T08:30:00Z").toISOString()).toBe("2024-02-29T08:30:00.000Z");
  });
});

describe("formatDay", () => {
  test("styles in the given locale", () => {
    expect(formatDay("2027-02-19", "en-GB", "long")).toBe("19 February 2027");
    expect(formatDay("2027-02-19", "en-GB", "medium")).toBe("19 Feb 2027");
    expect(formatDay("2027-02-19", "en-GB", "short")).toBe("19 Feb");
    expect(formatDay("2027-02-19", "en-GB", "dayMonth")).toBe("19 February");
    expect(formatDay("2027-02-19", "en-GB", "weekday")).toBe("Friday 19 February");
    expect(formatDay("2027-02-19", "en-GB", "monthYear")).toBe("February 2027");
    expect(formatDay("2027-02-19", "en-GB", "monthYearShort")).toBe("Feb 2027");
  });
  test("the locale decides the order", () => {
    expect(formatDay("2027-02-19", "en-US", "short")).toBe("Feb 19");
  });
  test("29 February is itself", () => {
    expect(formatDay("2024-02-29", "en-GB", "long")).toBe("29 February 2024");
  });
  test("an unknown or missing locale falls back to the message locale", () => {
    expect(formatDay("2027-02-19", "not a locale", "short")).toBe("19 Feb");
    expect(formatDay("2027-02-19", null, "short")).toBe("19 Feb");
  });
  test("a late-evening moment does not shift day in any time zone (UTC day)", () => {
    expect(formatDay("2026-03-29T23:30:00Z", "en-GB", "short")).toBe("29 Mar");
  });
});

describe("day arithmetic", () => {
  test("days between ignores daylight saving changes", () => {
    expect(daysBetween("2026-03-28", "2026-03-30")).toBe(2); // UK clocks go forward on the 29th
    expect(daysBetween("2026-10-24", "2026-10-26")).toBe(2); // and back on the 25th
    expect(daysBetween("2026-03-08", "2026-03-09")).toBe(1); // US change day
    expect(daysBetween("2026-11-01", "2026-11-02")).toBe(1);
  });
  test("days between is signed and spans a leap day", () => {
    expect(daysBetween("2024-02-28", "2024-03-01")).toBe(2);
    expect(daysBetween("2023-02-28", "2023-03-01")).toBe(1);
    expect(daysBetween("2026-01-10", "2026-01-01")).toBe(-9);
  });
  test("add days crosses months, years and the leap day", () => {
    expect(addDays("2024-02-28", 1)).toBe("2024-02-29");
    expect(addDays("2024-02-29", 1)).toBe("2024-03-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-29", -1)).toBe("2026-03-28");
  });
  test("add months and years clamp to the last day, never roll over", () => {
    expect(addYears("2024-02-29", -2)).toBe("2022-02-28");
    expect(addYears("2024-02-29", 4)).toBe("2028-02-29");
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-03-31", -1)).toBe("2026-02-28");
  });
});

describe("lists and numbers", () => {
  test("a list reads in the locale", () => {
    expect(formatList(["11", "4"], "en-GB")).toBe("11 and 4");
    expect(formatList(["a"], "en-GB")).toBe("a");
  });
  test("numbers carry the locale separator", () => {
    expect(formatNumber(3914, "en-GB")).toBe("3,914");
    expect(formatNumber(3914, "de-DE")).toBe("3.914");
    expect(formatNumber(3914, "bad locale!")).toBe("3,914");
  });
});

describe("bad input", () => {
  test("day arithmetic hands back what it was given instead of throwing", () => {
    expect(addDays("nonsense", 1)).toBe("nonsense");
    expect(addMonths("", 1)).toBe("");
    expect(addYears("2024-13-45x", 1)).toBe("2024-13-45x");
  });
});

describe("the signed-in person's locale", () => {
  test("is used when a screen passes none, and cleared on sign-out", () => {
    setSessionLocale("en-US");
    expect(formatDay("2024-03-05", undefined, "long")).toBe("March 5, 2024");
    expect(formatDay("2024-03-05", "en-GB", "long")).toBe("5 March 2024");
    setSessionLocale(null);
    expect(formatDay("2024-03-05", undefined, "long")).toBe("5 March 2024");
  });
});
