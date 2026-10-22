// S11 side panel: the entries opened together each show their date, so repeats of the same words can be told apart.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { TimelineInspector } from "./TimelineInspector";
import { event, FACT_1, FACT_2 } from "./testing";

test("each entry opened together shows its date", () => {
  const here = [event({ fact_id: FACT_1, event_date: "2024-03-01", summary: "Same words" }), event({ fact_id: FACT_2, event_date: "2024-06-03", summary: "Same words" })];
  render(
    <MemoryRouter>
      <TimelineInspector caseId={CASE_A} current={here[0]!} here={here} order={here} onSelect={() => undefined} onClose={() => undefined} />
    </MemoryRouter>,
  );
  const rows = within(screen.getByRole("list")).getAllByRole("button");
  expect(rows.map((r) => r.textContent)).toEqual(["1 Mar · Same words", "3 Jun · Same words"]);
});
