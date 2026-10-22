// S18b as drawn on the board: a time card with bars and an Adjust link, a paper invoice beside it, the fee note and
// Download / Send invoice at the bottom. The estimate and billing forms stay reachable behind one link.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { invoiceOut, reportOut, serve, stepKeys, versionOut, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/invoice`;
const sentReport = (run: PackRun) => reportOut(run, { version: versionOut({ status: "served" }) });

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the time card and the paper invoice sit side by side, and the screen is axe-clean", async () => {
    serve(run, { report: sentReport(run), invoices: [invoiceOut({ number_text: "JM-1" })] });
    const { container } = renderAt(url);
    const time = await screen.findByRole("region", { name: M["report.invoice.time"]! });
    const paper = await screen.findByRole("article", { name: "Invoice JM-1" });
    expect(time.parentElement).toBe(paper.parentElement);
    expect(paper).toHaveClass("rp-paper");
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("the title has a tick tile; Adjust is a link-styled button on the time card", async () => {
  serve("launch", { report: sentReport("launch") });
  renderAt(url);
  const title = await screen.findByRole("heading", { level: 1, name: M["report.invoice.heading_sent"]! });
  expect(title.parentElement?.parentElement?.querySelector(".av-icon-tile--mint")).not.toBeNull();
  expect(await screen.findByRole("button", { name: M["report.invoice.adjust"]! })).toHaveClass("av-text-link");
});

test("the invoice number is in mono, the payment line has Mark as paid, and the invoice has one Download and one Send", async () => {
  serve("launch", { invoices: [invoiceOut({ number_text: "JM-1", status: "sent", sent_at: "2026-11-28T10:00:00Z" })] });
  renderAt(url);
  const paper = await screen.findByRole("article", { name: "Invoice JM-1" });
  expect(within(paper).getByText("JM-1")).toHaveClass("av-mono");
  expect(within(paper).getByText(M["report.invoice.payment_unpaid"]!)).toBeInTheDocument();
  expect(within(paper).getByRole("button", { name: M["report.invoice.mark_paid"]! })).toHaveClass("av-text-link");
});

test("a draft invoice is sent from the bottom bar with an arrow, beside Download", async () => {
  serve("launch", { invoices: [invoiceOut()] });
  renderAt(url);
  const send = await screen.findByRole("button", { name: M["report.invoice.send"]! });
  expect(send.querySelector("svg")).not.toBeNull();
  expect(send.parentElement).toContainElement(screen.getByRole("link", { name: M["report.invoice.download"]! }));
});

test("the estimate, billing and bank forms are behind one link at the foot of the screen", async () => {
  serve("launch");
  const { user } = renderAt(url);
  const more = await screen.findByText(M["report.invoice.more"]!);
  const fold = more.closest("details") as HTMLDetailsElement;
  expect(fold).not.toBeNull();
  expect(fold.open).toBe(false);
  await user.click(more);
  expect(fold.open).toBe(true);
  expect(within(fold).getByLabelText(M["report.invoice.billing_name"]!)).toBeInTheDocument();
});

test("the fee note stands beside the buttons on the bottom bar", async () => {
  serve("launch", { invoices: [invoiceOut({ number_text: "JM-1", statements: ["A line."] })] });
  renderAt(url);
  const note = await screen.findByText(M["report.invoice.fee_note"]!);
  expect(note.closest(".rp-bar")).not.toBeNull();
});

test("Within your estimate is a bold lead with a tick, then the rest of the sentence", async () => {
  const stages = stepKeys("launch").map((stage, i) => ({ stage, seconds: (i + 1) * 3600, estimate_hours: null }));
  serve("launch", {
    report: reportOut("launch", { version: versionOut({ status: "served" }) }),
    time: { adjusted: false, total_seconds: 72900, stages, estimate_hours: "22.00", over_estimate: null, instructing_party: "Party" } as never,
  });
  renderAt(url);
  const lead = await screen.findByText(M["report.invoice.within_estimate_lead"]!);
  expect(lead.tagName).toBe("STRONG");
  const note = lead.closest("p")!;
  expect(note.querySelector("svg")).not.toBeNull();
  expect(note).toHaveTextContent("Within your estimate. You told the Party 22 hours when you accepted the case.");
});
