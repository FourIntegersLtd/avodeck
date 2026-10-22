import { describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
import { fact } from "./testData";
import { suggestTimelineDate } from "./timelineDate";

describe("suggestTimelineDate", () => {
  test("parses a two-digit year from the pack date field (DMY)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-10-21T12:00:00Z"));
    const f = fact(1, {
      event_date: null,
      fields: { date: "31.12.93", plan: "Microgynon cont 6/12" },
    });
    expect(suggestTimelineDate(f, launchPack)).toBe("1993-12-31");
    vi.useRealTimers();
  });

  test("uses event_date when already set", () => {
    const f = fact(1, { event_date: "2024-05-15", fields: { date: "31.12.93" } });
    expect(suggestTimelineDate(f, launchPack)).toBe("2024-05-15");
  });
});
