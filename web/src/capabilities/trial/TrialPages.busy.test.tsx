// S23: every action shows it is working and ignores a second click or file.
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";
import { expectBusyOnce, expectUploadBusyOnce, hold } from "./busyTesting";
import { blobReply, CASE, concordanceOut, rows, serveConcordance, term } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/trial-pages`;
const k = (key: string) => M[`trial.${key}`]!;

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:table"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

test("adding the court bundle shows it is sending and takes no second file", async () => {
  serveConcordance("launch", concordanceOut({ bundle: null, rows: [], counts: { matched: 0, to_confirm: 0, missing: 0 } }));
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/court-bundles", async () => (await h.enter(), json({ job_id: "a9999999-9999-4999-8999-999999999999", court_bundle_id: "a9333333-3333-4333-8333-333333333333" }, 202))));
  const { user } = renderAt(url);
  try {
    const bundle = term("launch", "court_bundle");
    await expectUploadBusyOnce(user, await screen.findByLabelText(k("pages.add").replace("{{court_bundle}}", bundle)), h);
  } finally { h.release(); }
});

test("telling the instructing party goes busy and sends once", async () => {
  serveConcordance("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/concordance/tell-instructing-party", async () => (await h.enter(), json({ missing: [], received_on: "2027-06-02", bundle_version: 2 }))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: /^Tell / }), h);
  } finally { h.release(); }
});

test("confirming a match goes busy and sends once", async () => {
  serveConcordance("launch");
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/concordance/:rowId", async () => (await h.enter(), json({ ...rows()[1]!, confirmed: true, status: "matched", version: 3 }))));
  const { user } = renderAt(url);
  const row = await screen.findByRole("row", { name: /p\.103/ });
  try {
    await expectBusyOnce(user, within(row).getByRole("button", { name: k("pages.confirm") }), h);
  } finally { h.release(); }
});

test("downloading the table as a PDF goes busy and sends once", async () => {
  serveConcordance("launch");
  const h = hold();
  server.use(http.get("/v1/cases/:caseId/concordance/export", async () => (await h.enter(), blobReply("application/pdf"))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: k("pages.export_pdf") }), h);
  } finally { h.release(); }
});

test("downloading the table as a spreadsheet shows it is working and sends once", async () => {
  serveConcordance("launch");
  const h = hold();
  server.use(http.get("/v1/cases/:caseId/concordance/export", async () => (await h.enter(), blobReply("text/csv"))));
  const { user } = renderAt(url);
  const link = await screen.findByRole("button", { name: k("pages.export_csv") });
  try {
    await expectBusyOnce(user, link, h);
  } finally { h.release(); }
  await waitFor(() => expect(link).not.toHaveAttribute("aria-busy"));
});
