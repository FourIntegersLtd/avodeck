import { describe, expect, test } from "vitest";
import { entry } from "./testing";
import { groupByPage, splitByVisitPage } from "./consultationEntries";

describe("splitByVisitPage", () => {
  test("puts facts on the visit page apart from the rest", () => {
    const a = entry({ page_no: 118 });
    const b = entry({ fact_id: "b", page_no: 117 });
    const { onPage, otherPages } = splitByVisitPage([a, b], 118);
    expect(onPage).toEqual([a]);
    expect(otherPages).toEqual([b]);
  });
});

describe("groupByPage", () => {
  test("groups and sorts by page number", () => {
    const a = entry({ page_no: 7 });
    const b = entry({ fact_id: "b", page_no: 1 });
    expect(groupByPage([a, b]).map((g) => g.page)).toEqual([1, 7]);
  });
});
