// While papers are being sent there is a visible control to stop; it cancels the upload and brings the screen back to ready.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, summary, theCase } from "./testData";

const UP = "a6666666-6666-4666-8666-666666666666";

test("Stop this upload cancels the upload, clears the record and returns to the ready state", async () => {
  signedIn({ pack: launchPack });
  let cancels = 0;
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase())),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(launchPack, { documents: [] }))),
    http.get(`/v1/cases/${CASE_ID}/progress`, () => json({ read: 0, total: 0, seconds_left: 0 })),
    http.get(`/v1/cases/${CASE_ID}/steps/progress`, () => json({ steps: [] })),
    http.get(`/v1/cases/${CASE_ID}/questions`, () => json({ questions: [], assumptions: [], locked: false, instruction_version_id: null })),
    http.post(`/v1/cases/${CASE_ID}/uploads`, () => json({ upload_id: UP, chunk_size: 8_388_608, chunk_count: 1, expires_at: new Date(Date.now() + 3600_000).toISOString() }, 201)),
    http.put("/v1/uploads/:id/chunks/:n", () => new Promise<Response>(() => undefined)),
    http.post("/v1/uploads/:id/cancel", () => (cancels++, json({ status: "cancelled" }))),
  );
  renderAt(`/cases/${CASE_ID}/papers/add`);
  const user = userEvent.setup();
  await screen.findByRole("heading", { name: "Add more papers" });
  expect(screen.queryByRole("button", { name: "Stop this upload" })).toBeNull();
  await user.upload(document.querySelector<HTMLInputElement>("input[type=file]")!, new File([new Uint8Array(300)], "a.pdf", { type: "application/pdf" }));
  await user.click(screen.getByRole("button", { name: "Add these papers" }));
  await user.click(await screen.findByRole("button", { name: "Stop this upload" }));
  await waitFor(() => expect(cancels).toBe(1));
  expect(await screen.findByRole("button", { name: "Add these papers" })).toBeEnabled();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(Object.keys(localStorage).filter((k) => k.startsWith("av.upload."))).toEqual([]);
});
