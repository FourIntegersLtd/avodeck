// S11 The board and the entry panel are one continuous surface with a divider between them, like the reader:
// the expert drags it (or uses the arrow keys) to give either side more room.
import { fireEvent, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { inCase, overview, timeline } from "./testing";

function serve() {
  server.use(
    http.get("/v1/cases/:caseId/timeline", () => json(timeline())),
    http.get("/v1/cases/:caseId/timeline/overview", () => json(overview())),
  );
}

test("the board and the panel sit either side of one divider the expert can move", async () => {
  inCase("launch");
  serve();
  renderAt(`/cases/${CASE_A}/timeline`);
  const board = await screen.findByTestId("timeline-board");
  const divider = screen.getByRole("separator", { name: "Make the timeline or the entry panel wider" });
  const surface = divider.parentElement!;
  expect(surface).toContainElement(board);
  expect(surface).toContainElement(screen.getByRole("complementary"));
  const before = Number(divider.getAttribute("aria-valuenow"));
  fireEvent.keyDown(divider, { key: "ArrowLeft" });
  expect(Number(divider.getAttribute("aria-valuenow"))).toBe(before - 2);
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(Number(divider.getAttribute("aria-valuenow"))).toBe(before + 2);
});

test("the divider stops before either side gets too narrow", async () => {
  inCase("launch");
  serve();
  renderAt(`/cases/${CASE_A}/timeline`);
  await screen.findByTestId("timeline-board");
  const divider = screen.getByRole("separator", { name: "Make the timeline or the entry panel wider" });
  for (let i = 0; i < 60; i++) fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(Number(divider.getAttribute("aria-valuenow"))).toBe(Number(divider.getAttribute("aria-valuemax")));
  for (let i = 0; i < 60; i++) fireEvent.keyDown(divider, { key: "ArrowLeft" });
  expect(Number(divider.getAttribute("aria-valuenow"))).toBe(Number(divider.getAttribute("aria-valuemin")));
});
