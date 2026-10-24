// Every action on screens 16, 17, 18 and 18b shows it is working: the button is disabled and busy while the request
// runs, a second click sends nothing, and content that is reloading behind old content says so.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { etagged, finalCheck, invoiceOut, PARA, profile, reportOut, sectionKeys, sectionLabel, serve, settingsOut, stepKeys, V1, V2, versionOut } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

/** A request that stays open until `release()`; `hits.n` counts how many reached the server. */
function hold(body: unknown, status = 200, headers?: Record<string, string>) {
  const hits = { n: 0 };
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const handler = async () => {
    hits.n += 1;
    await gate;
    return headers ? etagged(body, Number(headers.v), status) : json(body as never, status);
  };
  return { hits, release, handler };
}

async function expectBusy(button: HTMLElement) {
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAttribute("aria-busy", "true");
}

const sent = invoiceOut({ status: "sent", number_text: "JM-0001", sent_at: "2026-11-28T10:00:00Z", sent_to: "x@example.test" });
const withStatus = (status: string) => reportOut("launch", { version: versionOut({ status }), versions: [versionOut({ status })] });
const SUBMIT = /^Sign and send to the /;

test("sending a spot-check is busy and sends once", async () => {
  const h = hold({ id: "d1", token: "tok", fact_count: 10, expires_at: "2026-10-25T10:00:00Z" }, 201);
  serve("launch", { check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/spot-checks", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/final-check`);
  await user.click(await screen.findByRole("button", { name: M["report.check.spot"]! }));
  await user.type(await screen.findByLabelText(M["report.check.spot_email"]!), "colleague@example.test");
  const button = screen.getByRole("button", { name: M["report.check.spot_send"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  expect(await screen.findByText(M["report.check.spot_sent"]!)).toBeInTheDocument();
  expect(h.hits.n).toBe(1);
});

test("running the check again is busy and sends once", async () => {
  const h = hold({ job_id: "j1" }, 202);
  serve("launch", { check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/report/final-check", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/final-check`);
  const button = await screen.findByRole("button", { name: M["report.check.run_again"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("sending the invoice is busy and sends once", async () => {
  const h = hold({ ...sent });
  serve("launch", { invoices: [invoiceOut()] });
  server.use(http.post("/v1/cases/:caseId/invoices/:i/send", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  const button = await screen.findByRole("button", { name: M["report.invoice.send"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("marking an invoice paid is busy and sends once", async () => {
  const h = hold({ ...sent, status: "paid" });
  serve("launch", { invoices: [sent] });
  server.use(http.patch("/v1/cases/:caseId/invoices/:i/payment", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  const button = await screen.findByRole("button", { name: M["report.invoice.mark_paid"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("saving the time estimates is busy and sends once", async () => {
  const h = hold({ adjusted: false, total_seconds: 3600, estimate_hours: "3.00", over_estimate: null, stages: [] });
  const steps = stepKeys("launch");
  serve("launch", { time: { adjusted: false, total_seconds: 3600, estimate_hours: "2.00", over_estimate: null, stages: steps.map((stage, i) => ({ stage, seconds: i === 0 ? 3600 : 0, estimate_hours: i === 0 ? "2.00" : null, estimate_fee: null })) } });
  server.use(http.post("/v1/cases/:caseId/time/estimates", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  const region = await screen.findByRole("region", { name: M["report.invoice.estimate"]! });
  await user.type(within(region).getAllByLabelText(M["report.invoice.estimate_hours"]!)[1]!, "3");
  const button = within(region).getByRole("button", { name: M["report.invoice.estimate_save"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("saving a time adjustment is busy and sends once", async () => {
  const h = hold({ adjusted: true, total_seconds: 72600, stages: [] });
  serve("launch");
  server.use(http.patch("/v1/cases/:caseId/time/adjust", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.adjust"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["report.invoice.adjust_title"]! });
  await user.type(within(dialog).getByLabelText(M["report.invoice.adjust_minutes"]!), "-5");
  await user.type(within(dialog).getByLabelText(M["report.invoice.adjust_reason"]!), "A long pause");
  const button = within(dialog).getByRole("button", { name: M["report.invoice.adjust_save"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("saving billing details is busy and sends once", async () => {
  const h = hold({ profile: profile() }, 200, { v: "1" });
  serve("launch", { profile: null });
  server.use(http.put("/v1/me/billing-profile", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  await user.type(await screen.findByLabelText(M["report.invoice.billing_name"]!), "Dr Jane Morgan");
  await user.type(screen.getByLabelText(M["report.invoice.billing_currency"]!), "GBP");
  await user.type(screen.getByLabelText(M["report.invoice.billing_rate"]!), "180");
  const button = screen.getByRole("button", { name: M["report.invoice.billing_save"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("saving a credit note is busy and sends once", async () => {
  const h = hold({ id: "n1", amount: "100.00", reason: "Typo", issued_at: "2026-11-30T10:00:00Z", number_text: "CN-1" }, 201);
  serve("launch", { invoices: [sent] });
  server.use(http.post("/v1/cases/:caseId/invoices/:i/credit-notes", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/invoice`);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.credit"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["report.invoice.credit_title"]! });
  await user.type(within(dialog).getByLabelText(M["report.invoice.credit_amount"]!), "100");
  await user.type(within(dialog).getByLabelText(M["report.invoice.credit_reason"]!), "Typo");
  const button = within(dialog).getByRole("button", { name: M["report.invoice.credit_save"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("saving a section is busy, says saved only after, and sends once", async () => {
  const key = sectionKeys("launch")[0]!;
  const h = hold({ key, body: "My first words", citations: [], version: 6 }, 200, { v: "6" });
  serve("launch");
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => etagged({ key, body: "My first words", citations: [], version: 5 }, 5)),
    http.put("/v1/cases/:caseId/report/sections/:key", h.handler),
  );
  const { user } = renderAt(`/cases/${CASE_A}/report`);
  await user.click(await screen.findByRole("button", { name: `${M["report.report.section.edit"]} ${sectionLabel("launch", key)}` }));
  await screen.findByLabelText(`Your words for ${sectionLabel("launch", key)}`);
  const button = screen.getByRole("button", { name: M["report.report.section.save"]! });
  await user.click(button);
  await expectBusy(button);
  expect(screen.queryByText(M["report.report.section.saved"]!)).toBeNull();
  await user.click(button);
  h.release();
  expect(await screen.findByText(M["report.report.section.saved"]!)).toBeInTheDocument();
  expect(h.hits.n).toBe(1);
});

test("changing the citation style is busy while it saves", async () => {
  const h = hold(settingsOut({ citation_style: "paragraph", version: 4 }), 200, { v: "4" });
  serve("launch");
  server.use(http.patch("/v1/cases/:caseId/report/settings", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/report`);
  const other = await screen.findByRole("button", { name: M["report.report.style.paragraph"]! });
  await user.click(other);
  await waitFor(() => expect(other).toBeDisabled());
  await user.click(other);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("building the report is busy and sends once", async () => {
  const h = hold({ job_id: "j1" }, 202);
  serve("launch");
  server.use(http.post("/v1/cases/:caseId/report/build", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/report`);
  const button = await screen.findByRole("button", { name: M["report.report.rebuild"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("old paragraphs say they are updating while a new version loads", async () => {
  const key = sectionKeys("launch")[0]!;
  let built = false;
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  serve("launch");
  server.use(
    http.post("/v1/cases/:caseId/report/build", () => ((built = true), json({ job_id: "j1" }, 202))),
    http.get("/v1/cases/:caseId/report", () => {
      const next = versionOut({ id: V2, version_no: 2, display_name: "Draft 2" });
      return json(reportOut("launch", built ? { version: next, versions: [next, versionOut()] } : {}));
    }),
    http.get("/v1/cases/:caseId/report/paragraphs", async () => {
      calls += 1;
      if (calls > 1) await gate;
      return json({ version_id: V1, paragraphs: [{ id: PARA, para_no: 1, section: key, text: "Old paragraph", question_id: null }] } as never);
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/report`);
  expect(await screen.findByText("Old paragraph")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["report.report.rebuild"]! }));
  const paper = screen.getByRole("region", { name: M["report.report.paper"]! });
  await waitFor(() => expect(paper).toHaveAttribute("aria-busy", "true"));
  expect(screen.getByText("Old paragraph")).toBeInTheDocument();
  expect(screen.getByText(M["report.report.updating"]!)).toBeInTheDocument();
  release();
  await waitFor(() => expect(paper).toHaveAttribute("aria-busy", "false"));
});

test("reading the changed wording is busy and sends once", async () => {
  const h = hold({ acknowledged: "1" });
  serve("launch", { check: finalCheck() });
  server.use(
    http.post("/v1/cases/:caseId/report/sign", () => apiError(409, "declaration_not_acknowledged")),
    http.post("/v1/cases/:caseId/report/declarations/acknowledge", h.handler),
  );
  const { user } = renderAt(`/cases/${CASE_A}/sign`);
  await user.click(await screen.findByRole("checkbox", { name: /^I have read the whole report and I confirm the / }));
  await user.click(screen.getByRole("button", { name: SUBMIT }));
  const button = await screen.findByRole("button", { name: M["report.sign.acknowledge"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("marking the report as sent is busy and sends once", async () => {
  const h = hold({ id: V1, status: "served" });
  serve("launch", { report: withStatus("locked"), check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/report/versions/:v/serve", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/sign`);
  const date = await screen.findByLabelText(M["report.sign.served_on"]!);
  await user.clear(date);
  await user.type(date, "2026-11-28");
  const button = screen.getByRole("button", { name: M["report.sign.serve"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});

test("confirming a withdrawal is busy and sends once", async () => {
  const h = hold({ id: V1, status: "withdrawn" });
  serve("launch", { report: withStatus("served"), check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/report/versions/:v/withdraw", h.handler));
  const { user } = renderAt(`/cases/${CASE_A}/sign`);
  await user.click(await screen.findByRole("button", { name: M["report.sign.withdraw"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["report.sign.withdraw_title"]! });
  await user.type(within(dialog).getByLabelText(M["report.sign.withdraw_reason"]!), "Instructions ended");
  const button = within(dialog).getByRole("button", { name: M["report.sign.withdraw_confirm"]! });
  await user.click(button);
  await expectBusy(button);
  await user.click(button);
  h.release();
  await waitFor(() => expect(h.hits.n).toBe(1));
});
