// S05: every action shows it is working and ignores a second click while it runs.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { inCase, note } from "./testing";

const ASK = "Do you know any of the people in this case?";

function state(over: object = {}) {
  return {
    questions: [{ key: "knows_people", text: ASK, detail_required: false, answer: "no", detail: null }],
    complete: true,
    paragraph: null,
    coverage_question: null,
    coverage: { status: "covered", period_start: null, period_end: null },
    other_cases: { checked: 0, matches: 0, organisations: [] },
    warnings: [],
    declared_at: null,
    outcome: null,
    changed_since_declared: false,
    ...over,
  };
}

function open(over: object = {}) {
  inCase("launch");
  server.use(http.get("/v1/cases/:caseId/conflicts", () => json(state(over) as never)));
  return renderAt(`/cases/${CASE_A}/conflicts`);
}

const declared = () => json({ declared_at: "2026-10-17T09:00:00Z", outcome: "none", note: note("conflict_declared") });

test("confirming and continuing goes busy and declares once", async () => {
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/conflicts/declare", async () => (await h.enter(), declared())));
  const { user } = open();
  try {
    await expectBusyOnce(user, await byName("Confirm and continue"), h);
  } finally { h.release(); }
});

test("declaring a change goes busy and declares once", async () => {
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/conflicts/declare", async () => (await h.enter(), declared())));
  const { user } = open({ declared_at: "2026-10-05T09:00:00Z", changed_since_declared: true });
  try {
    await expectBusyOnce(user, await byName("Declare this change"), h);
  } finally { h.release(); }
});

test("saving changed answers goes busy and sends once", async () => {
  const h = hold();
  server.use(http.put("/v1/cases/:caseId/conflicts", async () => (await h.enter(), json(state() as never))));
  const { user } = open({ complete: false, questions: [{ key: "knows_people", text: ASK, detail_required: false, answer: null, detail: null }] });
  try {
    const group = await screen.findByRole("radiogroup", { name: ASK });
    await user.click(within(group).getByRole("radio", { name: "No" }));
    await expectBusyOnce(user, await byName("Confirm and continue"), h);
  } finally { h.release(); }
});

test("stepping back shows the step back button busy while it is recorded", async () => {
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/withdraw", async () => (await h.enter(), json({ status: "withdrawn", deadlines_cancelled: 0, note: note("withdrawal") }))));
  const { user } = open();
  try {
    const opener = await byName("Step back from this case");
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Step back from this case" });
    await user.type(within(dialog).getByLabelText("Why are you stepping back?"), "I know the family.");
    await user.click(within(dialog).getByRole("button", { name: "Step back" }));
    await waitFor(() => expect(h.count()).toBe(1));
    await waitFor(() => expect(opener).toHaveAttribute("aria-busy", "true"));
    expect(opener).toBeDisabled();
  } finally { h.release(); }
});
