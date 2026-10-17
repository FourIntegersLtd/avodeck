// S26 Close the case: keep your copies, how long we hold the records, proof of deletion, pause
// deletion, and closing. Pack words (retention years, the instructing party) come from the pack
// fixtures, so this file names none itself.
import { screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { CASE_A, fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderUi } from "../../test/render";
import { Close } from "./Close";
import { closeRoutes } from "./index";
import { CLOSE_MESSAGES } from "./words";

const EXPERT_ACTIONS = [
  "case.view_metadata", "case.export", "case.retention", "case.delete_now", "case.settle", "case.archive",
  "case.reopen", "legal_hold.set", "legal_hold.release", "case.view_certificate",
];
const ADMIN_ACTIONS = ["case.view_metadata", "case.export", "case.retention", "case.view_certificate"];

function theCase(over: Partial<Schemas["Case"]> = {}): Schemas["Case"] {
  return {
    allowed_actions: EXPERT_ACTIONS, case_type: "clin-neg-breach", court_track: null, created_at: "2026-01-05T09:00:00Z",
    due_date: null, expert_role: "single", id: CASE_A, instructing_party_type: null, jurisdiction: "gb-eaw",
    label: "Mrs A", named_expert_id: "11111111-1111-4111-8111-111111111111", on_hold: false,
    organisation_id: "22222222-2222-4222-8222-222222222222", party_role: null, specialty: "general-practice",
    status: "settled", updated_at: "2026-01-05T09:00:00Z", ...over,
  } as Schemas["Case"];
}

function summary(over: Partial<Schemas["CloseSummary"]> = {}): Schemas["CloseSummary"] {
  return {
    case_id: CASE_A, status: "settled", settled_at: "2027-07-14T10:00:00Z", retention_policy: "keep_default",
    delete_after: "2033-07-14", retention_years: launchPack.retention_years, ever_served: true, holds: [], held: false,
    deletion_started: false, deleted_at: null, job_id: null, ...over,
  };
}

type Sent = { method: string; path: string; body: unknown };

function serve(opts: { item?: Schemas["Case"]; close?: Schemas["CloseSummary"]; pack?: Schemas["CasePack"] } = {}) {
  signedIn({ pack: opts.pack });
  const sent: Sent[] = [];
  let close = opts.close ?? summary();
  const record = async (request: Request) => {
    const text = await request.text();
    sent.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
  };
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json(opts.item ?? theCase())),
    http.get(`/v1/cases/${CASE_A}/close`, () => json(close)),
    http.put(`/v1/cases/${CASE_A}/retention`, async ({ request }) => {
      await record(request);
      close = { ...close, retention_policy: (sent.at(-1)!.body as { policy: string }).policy };
      return json(close);
    }),
    http.post(`/v1/cases/${CASE_A}/settle`, async ({ request }) => {
      await record(request);
      close = { ...close, status: "settled" };
      return json(close);
    }),
    http.post(`/v1/cases/${CASE_A}/legal-holds`, async ({ request }) => {
      await record(request);
      const hold = { id: "66666666-6666-4666-8666-666666666666", reason: "appeal", until: null, set_by: theCase().named_expert_id, created_at: "2027-07-20T10:00:00Z", released_at: null };
      close = { ...close, holds: [hold], held: true };
      return json(hold, 201);
    }),
  );
  return { sent };
}

function renderClose() {
  return renderUi(
    <MemoryRouter initialEntries={[`/cases/${CASE_A}/close`]}>
      <Routes>
        <Route path="cases/:caseId/close" element={<Close />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("a settled case shows the three decisions with the pack's years, and passes axe", async () => {
  serve();
  const { container } = renderClose();
  expect(await screen.findByRole("heading", { level: 1, name: CLOSE_MESSAGES["close.heading.settled"] })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: CLOSE_MESSAGES["close.copies.heading"] })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: CLOSE_MESSAGES["close.keep.heading"] })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: CLOSE_MESSAGES["close.proof.heading"] })).toBeInTheDocument();
  const keep = screen.getByRole("radio", { name: new RegExp(`${launchPack.retention_years}`) });
  expect(keep).toBeChecked();
  expect(screen.getByText(/2033/)).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("delete now is not offered once a report was sent", async () => {
  serve();
  renderClose();
  const deleteNow = await screen.findByRole("radio", { name: new RegExp(CLOSE_MESSAGES["close.keep.delete_now"]!) });
  expect(deleteNow).toBeDisabled();
  expect(screen.getByText(CLOSE_MESSAGES["close.keep.delete_now_served"]!)).toBeInTheDocument();
});

test("the return choice names the instructing party in the pack's words and is saved", async () => {
  const party = fixturePack.instructing_party_types[0]!;
  const { sent } = serve({ pack: fixturePack, item: theCase({ instructing_party_type: party.key }) });
  const { user } = renderClose();
  const back = await screen.findByRole("radio", { name: new RegExp(party.label!) });
  await user.click(back);
  await user.click(screen.getByRole("button", { name: CLOSE_MESSAGES["close.keep.save"] }));
  await waitFor(() => expect(sent).toContainEqual({ method: "PUT", path: `/v1/cases/${CASE_A}/retention`, body: { policy: "return_then_delete" } }));
  expect(await screen.findByText(CLOSE_MESSAGES["close.keep.saved"]!)).toBeInTheDocument();
});

test("deleting now asks first", async () => {
  const { sent } = serve({ item: theCase({ status: "active" }), close: summary({ status: "active", settled_at: null, ever_served: false, retention_policy: null, delete_after: null }) });
  const { user } = renderClose();
  await user.click(await screen.findByRole("radio", { name: new RegExp(CLOSE_MESSAGES["close.keep.delete_now"]!) }));
  await user.click(screen.getByRole("button", { name: CLOSE_MESSAGES["close.keep.save"] }));
  const dialog = await screen.findByRole("dialog", { name: CLOSE_MESSAGES["close.keep.confirm_title"] });
  await user.click(within(dialog).getByRole("button", { name: CLOSE_MESSAGES["close.keep.confirm_yes"] }));
  await waitFor(() => expect(sent).toContainEqual({ method: "PUT", path: `/v1/cases/${CASE_A}/retention`, body: { policy: "delete_now" } }));
});

test("pausing deletion asks why and until when, then shows it is paused", async () => {
  const { sent } = serve();
  const { user } = renderClose();
  await user.click(await screen.findByRole("button", { name: CLOSE_MESSAGES["close.hold.pause"] }));
  const dialog = await screen.findByRole("dialog", { name: CLOSE_MESSAGES["close.hold.dialog_title"] });
  await user.click(within(dialog).getByRole("radio", { name: CLOSE_MESSAGES["close.hold.reason.appeal"] }));
  await user.click(within(dialog).getByRole("button", { name: CLOSE_MESSAGES["close.hold.confirm"] }));
  await waitFor(() => expect(sent).toContainEqual({ method: "POST", path: `/v1/cases/${CASE_A}/legal-holds`, body: { reason: "appeal", until: null } }));
  expect(await screen.findByText(CLOSE_MESSAGES["close.hold.paused"]!)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: CLOSE_MESSAGES["close.hold.resume"] })).toBeInTheDocument();
});

test("files are got ready, then downloaded once", async () => {
  serve();
  const job = "77777777-7777-4777-8777-777777777777";
  let polls = 0;
  server.use(
    http.post(`/v1/cases/${CASE_A}/export`, () => json({ job_id: job }, 202)),
    http.get(`/v1/jobs/${job}`, () => {
      polls += 1;
      return json({ id: job, case_id: CASE_A, kind: "export_case", status: polls > 1 ? "done" : "running", pct: null, step: null, eta_seconds: null, error_code: null, created_at: "2027-07-20T10:00:00Z", started_at: null, finished_at: null });
    }),
    http.get(`/v1/cases/${CASE_A}/export/${job}/download`, () => new Response(new Blob(["PK"]), { headers: { "content-type": "application/zip" } })),
  );
  const { user } = renderClose();
  await user.click(await screen.findByRole("button", { name: CLOSE_MESSAGES["close.copies.make"] }));
  const download = await screen.findByRole("button", { name: CLOSE_MESSAGES["close.copies.download"] }, { timeout: 5000 });
  await user.click(download);
  expect(await screen.findByText(CLOSE_MESSAGES["close.copies.downloaded"]!)).toBeInTheDocument();
});

test("an administrator is told the copy leaves out the medical records, and cannot settle", async () => {
  serve({ item: theCase({ allowed_actions: ADMIN_ACTIONS }) });
  renderClose();
  expect(await screen.findByText(CLOSE_MESSAGES["close.copies.admin_lead"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: CLOSE_MESSAGES["close.hold.pause"] })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: CLOSE_MESSAGES["close.close.archive"] })).not.toBeInTheDocument();
});

test("an open case is closed with the one main button", async () => {
  const { sent } = serve({ item: theCase({ status: "served" }), close: summary({ status: "served", settled_at: null, delete_after: null }) });
  const { user } = renderClose();
  await user.click(await screen.findByRole("button", { name: CLOSE_MESSAGES["close.close.settle"] }));
  await waitFor(() => expect(sent.map((s) => s.path)).toContain(`/v1/cases/${CASE_A}/settle`));
});

test("a refused step shows plain words, never the code", async () => {
  serve({ item: theCase({ status: "served" }), close: summary({ status: "served", settled_at: null, delete_after: null }) });
  server.use(http.post(`/v1/cases/${CASE_A}/settle`, () => apiError(409, "conflict")));
  const { user } = renderClose();
  await user.click(await screen.findByRole("button", { name: CLOSE_MESSAGES["close.close.settle"] }));
  expect(await screen.findByText(CLOSE_MESSAGES["close.error.conflict"]!)).toBeInTheDocument();
  expect(screen.queryByText(/conflict/)).not.toBeInTheDocument();
});

test("a deleted case shows its certificate: counts and dates only", async () => {
  serve({
    item: theCase({ status: "deleted" }),
    close: summary({ status: "deleted", deletion_started: true, deleted_at: "2033-07-14T02:00:00Z" }),
  });
  server.use(
    http.get(`/v1/cases/${CASE_A}/deletion-certificate`, () =>
      json({ case_id: CASE_A, deleted_at: "2033-07-14T02:00:00Z", counts: { pages: 1231, documents: 3, storage_objects: 1240 }, hashes: {}, log_chain_heads: {}, backup_expiry_on: "2033-08-13", sent_to: ["named_expert"] }),
    ),
  );
  renderClose();
  expect(await screen.findByRole("heading", { level: 1, name: CLOSE_MESSAGES["close.heading.deleted"] })).toBeInTheDocument();
  expect(await screen.findByText(/2033/, { selector: "dd" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: CLOSE_MESSAGES["close.keep.save"] })).not.toBeInTheDocument();
});

test("after deletion only the certificate answers, and the screen still shows it", async () => {
  serve();
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => apiError(404, "not_found")),
    http.get(`/v1/cases/${CASE_A}/pack`, () => apiError(404, "not_found")),
    http.get(`/v1/cases/${CASE_A}/close`, () => apiError(404, "not_found")),
    http.get(`/v1/cases/${CASE_A}/deletion-certificate`, () =>
      json({ case_id: CASE_A, deleted_at: "2033-07-14T02:00:00Z", counts: { pages: 1231, storage_objects: 1240 }, hashes: {}, log_chain_heads: {}, backup_expiry_on: "2033-08-13", sent_to: ["named_expert"] }),
    ),
  );
  renderClose();
  expect(await screen.findByRole("heading", { level: 1, name: CLOSE_MESSAGES["close.heading.deleted"] })).toBeInTheDocument();
  expect(await screen.findByText(/2033/, { selector: "dd" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
});

test("the screen is registered at the case's close path", () => {
  expect(closeRoutes.map((r) => r.path)).toEqual(["cases/:caseId/close"]);
});
