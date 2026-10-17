import { expect, test } from "vitest";
import { formatDay, formatDue, formatRelative, hasMessage, registerMessages, t } from ".";

test("interpolates parameters", () => {
  expect(t("today.greeting.morning", { name: "Dr Morgan" })).toBe("Good morning, Dr Morgan");
});

test("chooses the plural form from count", () => {
  expect(t("today.needs_you.heading", { count: 1 })).toBe("1 thing needs you");
  expect(t("today.needs_you.heading", { count: 3 })).toBe("3 things need you");
});

test("slices can register their own keys; a missing key falls back", () => {
  expect(hasMessage("example.thing.label")).toBe(false);
  registerMessages({ "example.thing.label": "{{case}} needs a look" });
  expect(t("example.thing.label", { case: "Mrs A" })).toBe("Mrs A needs a look");
  expect(t("example.missing.label", {}, "fallback")).toBe("fallback");
});

test("dates follow the locale's order and the person's timezone", () => {
  expect(formatDue("2026-11-30", "en-GB")).toBe("30 Nov");
  expect(formatDue("2026-11-30", "en-ZZ")).toBe("Nov 30");
  // 23:30 UTC on 13 October is already 14 October in London (BST).
  expect(formatDay(new Date("2026-10-13T23:30:00Z"), "en-GB", "Europe/London")).toBe("Wednesday 14 October");
});

test("relative times read naturally", () => {
  const now = new Date("2026-10-17T12:00:00Z");
  expect(formatRelative(new Date("2026-10-15T12:00:00Z"), "en-GB", now)).toBe("2 days ago");
});
