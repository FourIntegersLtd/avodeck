// S18b Time and invoice: time by workflow step (names from the pack), audited adjustments, the billing details,
// invoices from the time, sending behind the code, payments and credit notes.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { stepLabel } from "../../foundation/ui";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { casePack, etagged, INV, invoiceOut, profile, reportOut, serve, stepKeys, versionOut, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/invoice`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows your time for each step of the pack, the total, and passes axe", async () => {
    serve(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 1, name: M["report.invoice.title"] })).toBeInTheDocument();
    const time = await screen.findByRole("region", { name: M["report.invoice.time"]! });
    for (const [i, key] of stepKeys(run).entries()) {
      const row = within(time).getByRole("listitem", { name: stepLabel(key) });
      expect(within(row).getByText(`${Math.floor(((i + 1) * 600) / 3600)} h ${Math.floor((((i + 1) * 600) % 3600) / 60)} m`)).toBeInTheDocument();
    }
    expect(within(time).getByText("20 h 15 m")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("adjusting time sends the step, the seconds and the reason", async () => {
  const { sent, record } = serve("launch");
  server.use(http.patch("/v1/cases/:caseId/time/adjust", async ({ request }) => (await record(request), json({ adjusted: true, total_seconds: 72600, stages: [] }))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.adjust"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["report.invoice.adjust_title"]! });
  await user.selectOptions(within(dialog).getByLabelText(M["report.invoice.adjust_stage"]!), stepKeys("launch")[1]!);
  await user.type(within(dialog).getByLabelText(M["report.invoice.adjust_minutes"]!), "-5");
  await user.type(within(dialog).getByLabelText(M["report.invoice.adjust_reason"]!), "Took a long break");
  await user.click(within(dialog).getByRole("button", { name: M["report.invoice.adjust_save"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { stage: stepKeys("launch")[1], seconds: -300, reason: "Took a long break" } }));
});

test("with no billing details it says so and saves them with version 0", async () => {
  const { sent, record } = serve("launch", { profile: null });
  server.use(http.put("/v1/me/billing-profile", async ({ request }) => (await record(request), etagged({ profile: profile() }, 1))));
  const { user } = renderAt(url);
  expect(await screen.findByText(M["report.invoice.billing_missing"]!)).toBeInTheDocument();
  await user.type(screen.getByLabelText(M["report.invoice.billing_name"]!), "Dr Jane Morgan");
  await user.type(screen.getByLabelText(M["report.invoice.billing_currency"]!), "GBP");
  await user.type(screen.getByLabelText(M["report.invoice.billing_rate"]!), "180");
  await user.click(screen.getByRole("button", { name: M["report.invoice.billing_save"]! }));
  await waitFor(() =>
    expect(sent.at(-1)).toEqual({
      method: "PUT", path: "/v1/me/billing-profile", ifMatch: '"0"',
      body: { legal_name: "Dr Jane Morgan", address: null, tax_number: null, tax_rate: "0", currency: "GBP", hourly_rate: "180", invoice_prefix: "" },
    }),
  );
  expect(await screen.findByText(M["report.invoice.billing_saved"]!)).toBeInTheDocument();
});

test("saved billing details are filled in and changed with the version you saw", async () => {
  const { sent, record } = serve("launch");
  server.use(http.put("/v1/me/billing-profile", async ({ request }) => (await record(request), etagged({ profile: profile() }, 3))));
  const { user } = renderAt(url);
  const name = await screen.findByLabelText(M["report.invoice.billing_name"]!);
  expect(name).toHaveValue("Dr Jane Morgan");
  await user.clear(name);
  await user.type(name, "Jane Morgan Ltd");
  await user.click(screen.getByRole("button", { name: M["report.invoice.billing_save"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ ifMatch: '"2"', body: { legal_name: "Jane Morgan Ltd", hourly_rate: "180.00", invoice_prefix: "JM-" } }));
});

test("making the invoice sends the expenses and shows it as a draft with its total", async () => {
  const { sent, record } = serve("launch");
  let made = false;
  server.use(
    http.post("/v1/cases/:caseId/invoices", async ({ request }) => (await record(request), (made = true), json(invoiceOut(), 201))),
    http.get("/v1/cases/:caseId/invoices", () => json({ invoices: made ? [invoiceOut()] : [] })),
  );
  const { user } = renderAt(url);
  expect(await screen.findByText(M["report.invoice.none"]!)).toBeInTheDocument();
  await user.type(screen.getByLabelText(M["report.invoice.expenses"]!), "12.50");
  await user.click(screen.getByRole("button", { name: M["report.invoice.create"]! }));
  const card = await screen.findByRole("article", { name: M["report.invoice.draft"]! });
  expect(within(card).getByText("20.25 hours at £180.00")).toBeInTheDocument();
  expect(within(card).getAllByText("£3,645.00").length).toBeGreaterThan(0);
  expect(within(card).getByText(M["report.invoice.status.draft"]!)).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: M["report.invoice.download"]! })).toHaveAttribute("href", expect.stringContaining(`/v1/cases/${CASE_A}/invoices/${INV}/pdf`));
  expect(sent[0]).toMatchObject({ method: "POST", body: { expenses: "12.50" } });
});

test.each([["no_billing_profile"], ["no_hourly_rate"]])("making an invoice refused with %s is explained", async (code) => {
  serve("launch");
  server.use(http.post("/v1/cases/:caseId/invoices", () => apiError(409, code)));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.create"]! }));
  expect(await screen.findByText(M[`report.invoice.error.${code}`]!)).toBeInTheDocument();
});

test("sending asks for the code, then moves on to after serving", async () => {
  let sentOut = false;
  let stepped = false;
  serve("launch", { invoices: [invoiceOut()] });
  server.use(
    http.get("/v1/cases/:caseId/invoices", () => json({ invoices: [sentOut ? invoiceOut({ status: "sent", number_text: "JM-0001", sent_at: "2026-11-28T10:00:00Z", sent_to: "x@example.test" }) : invoiceOut()] })),
    http.post("/v1/cases/:caseId/invoices/:i/send", () => {
      if (!stepped) return apiError(403, "step_up_required");
      sentOut = true;
      return json(invoiceOut({ status: "sent", number_text: "JM-0001" }));
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user, router } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.send"]! }));
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "123456");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_A}/served`));
});

test("the fee note is always at the foot of an invoice", async () => {
  serve("launch", { invoices: [invoiceOut({ statements: [] })] });
  renderAt(url);
  expect(await screen.findByText(M["report.invoice.fee_note"]!)).toBeInTheDocument();
});

test("an invoice already sent says so", async () => {
  serve("launch", { invoices: [invoiceOut()] });
  server.use(http.post("/v1/cases/:caseId/invoices/:i/send", () => apiError(409, "already_sent")));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.invoice.send"]! }));
  expect(await screen.findByText(M["report.invoice.error.already_sent"]!)).toBeInTheDocument();
});

describe("a sent invoice", () => {
  const sentInvoice = invoiceOut({ status: "sent", number_text: "JM-0001", sent_at: "2026-11-28T10:00:00Z", sent_to: "x@example.test" });

  test("can be marked as paid or part paid", async () => {
    const { sent, record } = serve("launch", { invoices: [sentInvoice] });
    server.use(http.patch("/v1/cases/:caseId/invoices/:i/payment", async ({ request }) => (await record(request), json({ ...sentInvoice, status: "paid" }))));
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("button", { name: M["report.invoice.mark_paid"]! }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { status: "paid" } }));
    await user.type(screen.getByLabelText(M["report.invoice.part_amount"]!), "500");
    await user.click(screen.getByRole("button", { name: M["report.invoice.mark_part"]! }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ body: { status: "part_paid", paid_amount: "500" } }));
  });

  test("is corrected with a credit note, behind the code, never by editing", async () => {
    const { sent, record } = serve("launch", { invoices: [sentInvoice] });
    server.use(http.post("/v1/cases/:caseId/invoices/:i/credit-notes", async ({ request }) => (await record(request), json({ id: "n1", amount: "100.00", reason: "Rate typo", issued_at: "2026-11-30T10:00:00Z", number_text: "CN-1" }, 201))));
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("button", { name: M["report.invoice.credit"]! }));
    const dialog = await screen.findByRole("dialog", { name: M["report.invoice.credit_title"]! });
    await user.type(within(dialog).getByLabelText(M["report.invoice.credit_amount"]!), "100");
    await user.type(within(dialog).getByLabelText(M["report.invoice.credit_reason"]!), "Rate typo");
    await user.click(within(dialog).getByRole("button", { name: M["report.invoice.credit_save"]! }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", body: { amount: "100", reason: "Rate typo" } }));
    expect(screen.getByText(M["report.invoice.locked_note"]!)).toBeInTheDocument();
  });
});

describe.each<PackRun>(["launch", "fixture"])("the time estimate under the %s packs", (run) => {
  const party = () => String((casePack(run).instructing_party_types[0] as { label: string }).label);

  test("sends the hours and fee you estimate for each step and shows them against your time", async () => {
    const steps = stepKeys(run);
    const { sent, record } = serve(run, {
      time: { adjusted: false, total_seconds: 3600, estimate_hours: "2.00", over_estimate: null, stages: steps.map((stage, i) => ({ stage, seconds: i === 0 ? 3600 : 0, estimate_hours: i === 0 ? "2.00" : null, estimate_fee: null })) },
    });
    server.use(http.post("/v1/cases/:caseId/time/estimates", async ({ request }) => (await record(request), json({ adjusted: false, total_seconds: 3600, estimate_hours: "3.00", over_estimate: null, stages: [] }))));
    const { user } = renderAt(url);
    const region = await screen.findByRole("region", { name: M["report.invoice.estimate"]! });
    expect(within(region).getByText(M["report.invoice.estimate_total"]!.replace("{{hours}}", "2.00"))).toBeInTheDocument();
    const first = within(region).getByRole("group", { name: stepLabel(steps[0]!) });
    const hours = within(first).getByLabelText(M["report.invoice.estimate_hours"]!);
    expect(hours).toHaveValue("2.00");
    await user.clear(hours);
    await user.type(hours, "3");
    await user.type(within(first).getByLabelText(M["report.invoice.estimate_fee"]!), "250");
    await user.click(within(region).getByRole("button", { name: M["report.invoice.estimate_save"]! }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: "POST", path: `/v1/cases/${CASE_A}/time/estimates`, ifMatch: null, body: { estimates: [{ stage: steps[0], hours: "3", fee: "250" }] } }));
    expect(await screen.findByText(M["report.invoice.estimate_saved"]!)).toBeInTheDocument();
  });

  test("time over the estimate shows a gentle notice naming the instructing party, and nothing is blocked", async () => {
    serve(run, {
      invoices: [invoiceOut()],
      time: { adjusted: false, total_seconds: 43260, estimate_hours: "10.00", over_estimate: { percent: 20, instructing_party: party() }, stages: [] },
    });
    renderAt(url);
    const notice = await screen.findByRole("status", { name: M["report.invoice.over_estimate_title"]! });
    expect(notice).toHaveTextContent(M["report.invoice.over_estimate"]!.replace("{{percent}}", "20").replace("{{party}}", party()));
    expect(screen.getByRole("button", { name: M["report.invoice.send"]! })).toBeEnabled();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });
});

test("an invoice with an estimate states it against the total in the invoice's currency", async () => {
  serve("launch", { invoices: [invoiceOut({ currency: "XTS", estimate_amount: "2300.00", statements: [] })] });
  renderAt(url);
  const card = await screen.findByRole("article", { name: M["report.invoice.draft"]! });
  const line = within(card).getByText((_, el) => el?.getAttribute("data-estimate") === "true");
  expect(line.textContent).toContain("2,300.00");
  expect(line.textContent).toContain("3,645.00");
  expect(line.textContent).toContain("XTS");
  expect(line.textContent).not.toContain("£");
});

test("bank details are saved behind the code with the version you saw", async () => {
  let stepped = false;
  const { sent, record } = serve("launch");
  server.use(
    http.put("/v1/me/billing-profile/bank-details", async ({ request }) => {
      if (!stepped) return apiError(403, "step_up_required");
      await record(request);
      return etagged({ ...profile(), bank: { account_name: "Jane Morgan Ltd", sort_code: "123456", account_number: "12345678", iban: null, bic: null } }, 3);
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user } = renderAt(url);
  const bank = await screen.findByRole("region", { name: M["report.invoice.bank"]! });
  expect(within(bank).getByText(M["report.invoice.bank_note"]!)).toBeInTheDocument();
  await user.type(within(bank).getByLabelText(M["report.invoice.bank_account_name"]!), "Jane Morgan Ltd");
  await user.type(within(bank).getByLabelText(M["report.invoice.bank_sort_code"]!), "12-34-56");
  await user.type(within(bank).getByLabelText(M["report.invoice.bank_account_number"]!), "12345678");
  await user.click(within(bank).getByRole("button", { name: M["report.invoice.bank_save"]! }));
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "123456");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  await waitFor(() =>
    expect(sent.at(-1)).toEqual({
      method: "PUT", path: "/v1/me/billing-profile/bank-details", ifMatch: '"2"',
      body: { account_name: "Jane Morgan Ltd", sort_code: "12-34-56", account_number: "12345678", iban: null, bic: null },
    }),
  );
  expect(await within(bank).findByText(M["report.invoice.billing_saved"]!)).toBeInTheDocument();
});

test("once the report is sent, the title says so and shows the bars and the within-estimate note", async () => {
  const stages = stepKeys("launch").map((stage, i) => ({ stage, seconds: (i + 1) * 3600, estimate_hours: null }));
  serve("launch", {
    report: reportOut("launch", { version: versionOut({ status: "served" }) }),
    time: { adjusted: false, total_seconds: 72900, stages, estimate_hours: "22.00", over_estimate: null, instructing_party: "Party" } as never,
  });
  renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: M["report.invoice.heading_sent"]! })).toBeInTheDocument();
  const bars = await screen.findAllByRole("meter");
  expect(bars).toHaveLength(stages.length);
  expect(bars[0]).toHaveAttribute("aria-label", stepLabel(stages[0]!.stage));
  expect(bars[0]).toHaveAttribute("value", "3600");
  expect(screen.getByText("Within your estimate. You told the Party 22 hours when you accepted the case.")).toBeInTheDocument();
});

test("an invoice shows who it is from and to, the case, the date and the pack's statements, with the fee note", async () => {
  serve("launch", { invoices: [invoiceOut({ number_text: "JM-1", sent_to: "who@example.test", statements: ["First line.", "Second line."] })] });
  renderAt(url);
  const invoice = await screen.findByRole("article", { name: "Invoice JM-1" });
  expect(within(invoice).getByText("From: Dr Jane Morgan")).toBeInTheDocument();
  expect(within(invoice).getByText("To: who@example.test")).toBeInTheDocument();
  expect(within(invoice).getByText("Case: Mrs A")).toBeInTheDocument();
  expect(within(invoice).getByText("Date: 18 October 2026")).toBeInTheDocument();
  expect(within(invoice).getByText("First line.")).toBeInTheDocument();
  expect(within(invoice).getByText("Second line.")).toBeInTheDocument();
  expect(screen.getByText(M["report.invoice.fee_note"]!)).toBeInTheDocument();
});

test("without statements there is no fee note", async () => {
  serve("launch", { invoices: [invoiceOut({ number_text: "JM-1" })] });
  renderAt(url);
  await screen.findByRole("article", { name: "Invoice JM-1" });
  expect(screen.queryByText(M["report.invoice.fee_note"]!)).toBeNull();
});

test("once sent, the intro says who it went to and when, from the serving record", async () => {
  const serving = { id: "s1", sent_via: "email", served_on: "2026-10-18", created_at: "2026-10-18T10:00:00Z", correction_of: null, reason: null, recipient_party_id: null } as never;
  serve("launch", {
    report: reportOut("launch", { version: versionOut({ status: "served" }) }),
    servings: [serving],
    time: { adjusted: false, total_seconds: 0, stages: [], instructing_party: "Party" } as never,
  });
  renderAt(url);
  expect(await screen.findByText("Sent to the Party on 18 October 2026. We kept count of your time as you worked.")).toBeInTheDocument();
});
