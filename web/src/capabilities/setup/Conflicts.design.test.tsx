// S05 as drawn on the design board: the role-coverage question "from your profile", the bundle-preparing
// footer linked to the Preparing screen, and one "Confirm and continue" that saves, declares and moves on.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { inCase, launchWords, note, type PackRun } from "./testing";

const questionsList = [
  { key: "knows_people", text: "Do you know any of the people in this case?", detail_required: true, answer: "no", detail: null },
  { key: "holds_a_stake", text: "Do you hold a stake in the result?", detail_required: false, answer: "no", detail: null },
];

function state(over: Record<string, unknown> = {}) {
  return {
    questions: questionsList,
    complete: true,
    paragraph: "I confirm I have no conflict.",
    coverage_question: "Were you working throughout the period of the case?",
    coverage: { status: "covered", period_start: "2024-02-01", period_end: "2024-08-31" },
    warnings: [],
    declared_at: null,
    outcome: null,
    ...over,
  };
}

function open(run: PackRun, over: Record<string, unknown> = {}) {
  inCase(run);
  server.use(http.get("/v1/cases/:caseId/conflicts", () => json(state(over))));
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the role-coverage question shows the pack's words and where the answer comes from", async () => {
    open(run);
    const { container } = renderAt(`/cases/${CASE_A}/conflicts`);
    const row = await screen.findByRole("group", { name: "Were you working throughout the period of the case?" });
    expect(within(row).getByText("From your profile")).toBeInTheDocument();
    expect(within(row).getByText("Yes")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("a part-covered period says so beside the coverage question", async () => {
  open("launch", { coverage: { status: "partial", period_start: null, period_end: null } });
  renderAt(`/cases/${CASE_A}/conflicts`);
  const row = await screen.findByRole("group", { name: "Were you working throughout the period of the case?" });
  expect(within(row).getByText("Part of it")).toBeInTheDocument();
});

test("while the bundle is being prepared the footer says how long is left and links to it", async () => {
  open("launch");
  server.use(http.get("/v1/cases/:caseId/process", () => json({ job: { id: "e1111111-1111-4111-8111-111111111111", kind: "process_bundle", status: "running", step: "ocr", pct: 40, eta_seconds: 240, error_code: null } })));
  renderAt(`/cases/${CASE_A}/conflicts`);
  expect(await screen.findByText("While we prepare your bundle")).toBeInTheDocument();
  const link = await screen.findByRole("link", { name: "Your bundle is still being prepared: about 4 minutes left" });
  expect(link).toHaveAttribute("href", `/cases/${CASE_A}/preparing`);
});

test("with no job running the footer says nothing about preparing", async () => {
  open("launch");
  renderAt(`/cases/${CASE_A}/conflicts`);
  await screen.findByRole("button", { name: "Confirm and continue" });
  expect(screen.queryByText(/still being prepared/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Save my answers" })).toBeNull();
});

test("Confirm and continue saves the answers, declares them and opens the questions", async () => {
  open("launch", { complete: false, paragraph: null, questions: questionsList.map((q) => ({ ...q, answer: null })) });
  const calls: string[] = [];
  server.use(
    http.put("/v1/cases/:caseId/conflicts", () => {
      calls.push("put");
      return json(state());
    }),
    http.post("/v1/cases/:caseId/conflicts/declare", () => {
      calls.push("declare");
      return json({ declared_at: "2026-10-17T09:00:00Z", outcome: "none", note: note("conflict_declared") });
    }),
    http.get("/v1/cases/:caseId/questions", () => json({ questions: [], assumptions: [], locked: false, reading: true, reopened: false, rejected: 0, warnings: [], version_no: 1, instruction_version_id: null })),
  );
  const { user, router } = renderAt(`/cases/${CASE_A}/conflicts`);
  for (const name of ["Do you know any of the people in this case?", "Do you hold a stake in the result?"]) {
    await user.click(within(await screen.findByRole("radiogroup", { name })).getByRole("radio", { name: "No" }));
  }
  await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_A}/questions`));
  expect(calls).toEqual(["put", "declare"]);
});
