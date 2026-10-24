// S11 While a new window or filter is loading, the expert is told: the old lanes stay, dimmed and marked busy,
// with "Updating the timeline…" beside them, until the new answer arrives.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { inCase, overview, timeline } from "./testing";

test("changing the window shows that the timeline is updating until the new lanes arrive", async () => {
  inCase("launch");
  let release: () => void = () => {};
  let hold = false;
  server.use(
    http.get("/v1/cases/:caseId/timeline", async () => {
      if (hold) await new Promise<void>((r) => (release = r));
      return json(timeline());
    }),
    http.get("/v1/cases/:caseId/timeline/overview", () => json(overview())),
  );
  const { user } = renderAt(`/cases/${CASE_A}/timeline`);
  const board = await screen.findByTestId("timeline-board");
  expect(screen.queryByText("Updating the timeline…")).not.toBeInTheDocument();

  hold = true;
  await user.click(screen.getByRole("button", { name: "Whole record" }));
  try {
    const note = await screen.findByText("Updating the timeline…");
    expect(note.closest("[role=status]")).not.toBeNull();
    expect(board.closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  } finally {
    release();
  }

  await waitFor(() => expect(screen.queryByText("Updating the timeline…")).not.toBeInTheDocument());
  expect((await screen.findByTestId("timeline-board")).closest("[aria-busy]")).toHaveAttribute("aria-busy", "false");
}, 15_000);
