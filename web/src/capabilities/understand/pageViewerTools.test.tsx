// S11 and S12 show the page with the shared viewer: zoom, turn and fit, and no brightness or contrast.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { consultation, FACT_1, inCase } from "./testing";
import { forgetPages } from "./pageImage";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/consultations/:factId", () => HttpResponse.json(consultation())),
    http.get("/v1/cases/:caseId/consultations/:factId/later-notes", () => HttpResponse.json({ notes: [] })),
    http.get("/v1/cases/:caseId/comments", () => HttpResponse.json({ comments: [] })),
    http.get("/v1/cases/:caseId/pages/:pageNo/image", () => new HttpResponse(new Uint8Array([255, 216, 255]), { headers: { "content-type": "image/jpeg" } })),
  );
});
afterEach(() => {
  forgetPages();
  vi.unstubAllGlobals();
});

test("the consultation page has zoom, turn and fit, but no clearer controls, and zoom changes the level", async () => {
  const user = userEvent.setup();
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  await waitFor(() => expect(screen.getByRole("img", { name: "Page 118 of the record" })).toBeTruthy());
  for (const name of ["Zoom in", "Zoom out", "Turn left", "Turn right", "Fit to width", "Whole page"]) {
    expect(screen.getByRole("button", { name })).toBeTruthy();
  }
  expect(screen.queryByLabelText("Brightness")).toBeNull();
  expect(screen.queryByLabelText("Contrast")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText("125%")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Turn right" }));
  expect(screen.getByTestId("page-turn").getAttribute("data-turn")).toBe("90");
});
