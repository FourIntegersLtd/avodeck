// The calm design on S16 to S18b: one green (primary) button per screen, choices of one kind in a
// SegmentedControl, the other actions soft grey, notices borderless.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { failedCheck, finalCheck, invoiceOut, reportOut, serve, versionOut } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

/** The solid green buttons: an `av-button` with no variant class (a disabled one still counts). */
const primaries = () => [...document.querySelectorAll<HTMLElement>(".av-button")].filter((el) => !["secondary", "outline", "quiet", "link", "danger"].some((v) => el.classList.contains(`av-button--${v}`)));
const served = () => reportOut("launch", { version: versionOut({ status: "served" }), versions: [versionOut({ status: "served" })] });

describe("one primary button per screen", () => {
  test("S16 with a report: only the move to the final check", async () => {
    serve("launch");
    renderAt(`/cases/${CASE_A}/report`);
    await screen.findByRole("link", { name: M["report.report.final_check"]! });
    expect(primaries()).toHaveLength(1);
    expect(primaries()[0]).toHaveTextContent(M["report.report.final_check"]!);
  });

  test("S16 with no report yet: only Build", async () => {
    serve("launch", { report: reportOut("launch", { version: null, versions: [] }) });
    renderAt(`/cases/${CASE_A}/report`);
    await screen.findByRole("button", { name: M["report.report.build"]! });
    expect(primaries()).toHaveLength(1);
  });

  test("S17 with something to fix: Fix it is soft grey and only the (disabled) Sign button is green", async () => {
    serve("launch", { check: failedCheck() });
    renderAt(`/cases/${CASE_A}/final-check`);
    const fix = await screen.findByRole("link", { name: M["report.check.fix"]! });
    expect(fix).toHaveClass("av-button--secondary");
    expect(primaries()).toHaveLength(1);
    expect(primaries()[0]).toBeDisabled();
  });

  test("S17 clean: only Sign and send", async () => {
    serve("launch", { check: finalCheck() });
    renderAt(`/cases/${CASE_A}/final-check`);
    await screen.findByRole("link", { name: M["report.check.sign"]! });
    expect(primaries()).toHaveLength(1);
  });

  test("S18 ready to sign: only Sign and send", async () => {
    serve("launch", { check: finalCheck() });
    const { container } = renderAt(`/cases/${CASE_A}/sign`);
    await screen.findByRole("checkbox");
    expect(primaries()).toHaveLength(1);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("S18 after serving: only the move to the invoice", async () => {
    serve("launch", { report: served(), check: finalCheck() });
    renderAt(`/cases/${CASE_A}/sign`);
    await screen.findByRole("link", { name: M["report.sign.invoice"]! });
    expect(primaries()).toHaveLength(1);
  });

  test("S18b with a draft invoice: only Send invoice", async () => {
    serve("launch", { report: served(), invoices: [invoiceOut()] });
    renderAt(`/cases/${CASE_A}/invoice`);
    await screen.findByRole("button", { name: M["report.invoice.send"]! });
    expect(primaries()).toHaveLength(1);
  });
});

test("S16 citation style is a SegmentedControl of pressed buttons", async () => {
  serve("launch");
  renderAt(`/cases/${CASE_A}/report`);
  const group = await screen.findByRole("group", { name: M["report.report.style"]! });
  expect(group).toHaveClass("av-segmented-control");
  expect(within(group).getByRole("button", { name: M["report.report.style.page"]! })).toHaveAttribute("aria-pressed", "true");
});

test("S16 warnings are amber notices", async () => {
  serve("launch", { report: served() });
  renderAt(`/cases/${CASE_A}/report`);
  const note = await screen.findByText(M["report.report.served"]!);
  expect(note.closest(".av-notice")).toHaveClass("av-notice--amber");
});
