// S11 count line: entries drawn on a series bar are on screen, so they count as shown and in the total.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { TimelineBoard } from "./TimelineBoard";
import { event } from "./testing";

test("entries on a bar are counted as shown and in the total", () => {
  const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  const issues = [1, 2, 3].map((n) => event({ fact_id: id(n), kind: "zz_issue", event_date: `2024-0${n + 2}-01`, series_key: "lane_one:0" }));
  const lane = {
    key: "lane_one",
    label: "Lane one words",
    events: [...issues, event({ fact_id: id(9), event_date: "2024-05-20" })],
    series: [{ key: "lane_one:0", label: "Bar words", first: "2024-03-01", last: "2024-05-01", count: 3, fact_ids: issues.map((e) => e.fact_id) }],
  };
  render(
    <MemoryRouter>
      <TimelineBoard caseId={CASE_A} data={{ lanes: [lane], unplaced: [] }} from="2024-02-01" to="2024-08-31" onOpen={() => undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByText(/^Showing 4 of 4 entries/)).toBeInTheDocument();
});
