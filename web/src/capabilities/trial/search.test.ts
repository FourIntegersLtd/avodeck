// The witness-box search (S25) runs on the device with no connection: a page number (yours or the court
// bundle's), a date in the case's date order or written out, or words, across the served report, the
// verified facts, the meeting statement and the written answers.
import { describe, expect, test } from "vitest";
import { search } from "./search";
import { copyContent } from "./testing";

const copy = copyContent();

describe("search", () => {
  test("nothing typed finds nothing", () => {
    expect(search(copy, "  ", "en-GB")).toEqual([]);
  });

  test("a page number finds everything on that page, by your number or the court bundle's", () => {
    for (const query of ["118", "p.118", "2407"]) {
      const found = search(copy, query, "en-GB");
      expect(found.map((r) => r.kind).sort()).toEqual(["fact", "paragraph"]);
      expect(found.every((r) => r.page_no === 118 && r.trial_page_no === 2407)).toBe(true);
    }
  });

  test("a date is read in the case's date order", () => {
    expect(search(copy, "15/05/2024", "en-GB").map((r) => r.text)).toEqual(["Bleeding reported, no examination"]);
    const mdy = copyContent({ date_order: "MDY" });
    expect(search(mdy, "05/15/2024", "en-US").map((r) => r.text)).toEqual(["Bleeding reported, no examination"]);
    expect(search(mdy, "15/05/2024", "en-US")).toEqual([]);
  });

  test("a written-out date finds the fact on that day", () => {
    const found = search(copy, "15 May 2024", "en-GB");
    expect(found[0]).toMatchObject({ kind: "fact", date: "2024-05-15", page_no: 118 });
  });

  test("words must all appear, in any case", () => {
    expect(search(copy, "IRON started", "en-GB")).toEqual([expect.objectContaining({ kind: "paragraph", ref: 4, page_no: 103, trial_page_no: null })]);
    expect(search(copy, "haemoglobin", "en-GB")).toEqual([expect.objectContaining({ kind: "answer", ref: 2 })]);
    expect(search(copy, "examine", "en-GB")).toEqual([expect.objectContaining({ kind: "item", ref: 1 })]);
    expect(search(copy, "examination bleeding", "en-GB").map((r) => r.kind).sort()).toEqual(["fact", "item", "paragraph"]);
  });
});
