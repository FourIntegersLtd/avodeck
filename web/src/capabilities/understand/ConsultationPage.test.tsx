// S12 shows the day's page as an object URL straight from the picture the API streams, and coming back to
// the same page a moment later shows it again without fetching (and decrypting) it a second time.
import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { consultation, FACT_1, inCase } from "./testing";
import { forgetPages } from "./pageImage";

let fetched = 0;

beforeEach(() => {
  fetched = 0;
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => `blob:page-${fetched}`), revokeObjectURL: vi.fn() }));
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/consultations/:factId", () => HttpResponse.json(consultation())),
    http.get("/v1/cases/:caseId/consultations/:factId/later-notes", () => HttpResponse.json({ notes: [] })),
    http.get("/v1/cases/:caseId/comments", () => HttpResponse.json({ comments: [] })),
    http.get("/v1/cases/:caseId/pages/:pageNo/image", () => {
      fetched++;
      return new HttpResponse(new Uint8Array([255, 216, 255]), { headers: { "content-type": "image/jpeg" } });
    }),
  );
});
afterEach(() => {
  forgetPages();
  vi.unstubAllGlobals();
});

test("the page is shown from an object URL and is not fetched again when the screen is opened again", async () => {
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  await waitFor(() => expect(screen.getByRole("img", { name: "Page 118 of the record" })).toHaveAttribute("src", "blob:page-1"));
  expect(fetched).toBe(1);
  cleanup();
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  await waitFor(() => expect(screen.getByRole("img", { name: "Page 118 of the record" })).toHaveAttribute("src", "blob:page-1"));
  expect(fetched).toBe(1);
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
});

test("a page the API cannot give says so", async () => {
  server.use(http.get("/v1/cases/:caseId/pages/:pageNo/image", () => HttpResponse.json({ code: "not_found" }, { status: 404 })));
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  expect(await screen.findByText("The page picture is not available.")).toBeVisible();
});
