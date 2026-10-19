// Audit fixes for slice E: the pack's own screen key, the way on from the last question, the readable
// copy of citations, and the glossary word for the cite failure.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { screenPath } from "../../foundation/lib/screens";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import "./index";
import { FACT_1, opinionOut, PAGE_1, Q1, Q2, serve, workspace } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/opinion?q=${Q1}`;

test("the pack's points_against key opens the points screen", () => {
  expect(screenPath("points_against", { caseId: CASE_A })).toBe(`/cases/${CASE_A}/points`);
});

test("with no next question the opinion screen leads on to the report, and still offers the way back", async () => {
  serve("launch");
  renderAt(`/cases/${CASE_A}/opinion?q=${Q2}`);
  await screen.findByRole("heading", { level: 2, name: M["write.opinion.title"] });
  expect(screen.queryByRole("link", { name: M["write.opinion.next"]! })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["write.opinion.go_report"]! })).toHaveAttribute("href", `/cases/${CASE_A}/report`);
  expect(screen.getByRole("link", { name: M["write.opinion.back_to_points"]! })).toBeInTheDocument();
});

test("citation markers are also shown as page numbers in a readable copy when nothing is flagged", async () => {
  const ref = { fact_id: FACT_1, page_id: PAGE_1, quote_verbatim: "bleeding for three weeks", fact_version: 2 };
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "It began early [[cite:1]].", citation_refs: [ref], lint: [] }) }) });
  renderAt(url);
  const copy = await screen.findByRole("region", { name: M["write.opinion.readable_copy"] });
  expect(within(copy).getByText("It began early p.118.")).toBeInTheDocument();
  expect(within(copy).queryByText(/\[\[cite/)).not.toBeInTheDocument();
});

test("the cite failure uses the glossary word, not 'verified'", () => {
  expect(M["write.opinion.cite_failed"]).not.toMatch(/verified/i);
  expect(M["write.opinion.cite_failed"]).toMatch(/checked/i);
});
