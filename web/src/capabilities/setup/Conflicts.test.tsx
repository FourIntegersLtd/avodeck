// S05 Before you start: the pack's conflict questions, details required on "yes", the paragraph the
// answers produce, the coverage warning, declaring something new mid-case and stepping back (with the code).
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, inCase, launchWords, note, type PackRun } from "./testing";

type State = {
  questions: Array<{ key: string; text: string; detail_required: boolean; answer: "yes" | "no" | null; detail: string | null }>;
  complete: boolean;
  paragraph: string | null;
  coverage: { status: "covered" | "partial" | "none" | "unknown"; period_start: string | null; period_end: string | null };
  warnings: string[];
  declared_at: string | null;
  outcome: "none" | "disclosed" | null;
};

function state(over: Partial<State> = {}): State {
  return {
    questions: [
      { key: "knows_people", text: "Do you know any of the people in this case?", detail_required: true, answer: null, detail: null },
      { key: "holds_a_stake", text: "Do you hold a stake in the result?", detail_required: false, answer: null, detail: null },
    ],
    complete: false,
    paragraph: null,
    coverage: { status: "partial", period_start: "2024-02-01", period_end: "2024-08-31" },
    warnings: ["setup.conflicts.warning.coverage_partial"],
    declared_at: null,
    outcome: null,
    ...over,
  };
}

function conflicts(run: PackRun, over: Partial<State> = {}) {
  inCase(run);
  server.use(http.get("/v1/cases/:caseId/conflicts", () => json(state(over))));
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("asks each question with yes or no, warns about coverage, shows your balance, and passes axe", async () => {
    conflicts(run);
    const { container } = renderAt(`/cases/${CASE_A}/conflicts`);
    expect(await screen.findByRole("heading", { level: 2, name: "A quick check before you start" })).toBeInTheDocument();
    const first = await screen.findByRole("radiogroup", { name: "Do you know any of the people in this case?" });
    expect(within(first).getByRole("radio", { name: "Yes" })).toBeInTheDocument();
    expect(within(first).getByRole("radio", { name: "No" })).toBeInTheDocument();
    expect(screen.getByText("The roles in your profile cover only part of the time this case is about. Please check you worked in this kind of setting then.")).toBeInTheDocument();
    const balance = screen.getByRole("region", { name: "Your balance over the last 12 months" });
    const other = casePack(run).party_roles.find((r) => r.key === "other_side")!.label!;
    expect(await within(balance).findByText(`for the ${other}`)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("a yes needs a few words of detail before the answers are saved", async () => {
  conflicts("launch");
  let sent: unknown;
  server.use(
    http.put("/v1/cases/:caseId/conflicts", async ({ request }) => {
      sent = await request.json();
      return json(state({ complete: true, paragraph: "I disclose the following. I met them once in 2019." }));
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/conflicts`);
  const first = await screen.findByRole("radiogroup", { name: "Do you know any of the people in this case?" });
  await user.click(within(first).getByRole("radio", { name: "Yes" }));
  const second = screen.getByRole("radiogroup", { name: "Do you hold a stake in the result?" });
  await user.click(within(second).getByRole("radio", { name: "No" }));
  await user.click(screen.getByRole("button", { name: "Save my answers" }));
  expect(await screen.findByText("Please say a few words about this.")).toBeInTheDocument();
  expect(sent).toBeUndefined();

  await user.type(screen.getByLabelText("Please tell us more"), "I met them once in 2019.");
  await user.click(screen.getByRole("button", { name: "Save my answers" }));
  await waitFor(() =>
    expect(sent).toEqual({
      answers: [
        { key: "knows_people", answer: "yes", detail: "I met them once in 2019." },
        { key: "holds_a_stake", answer: "no" },
      ],
    }),
  );
  const paragraph = await screen.findByRole("region", { name: "What we'll add to your report" });
  expect(within(paragraph).getByText("I disclose the following. I met them once in 2019.")).toBeInTheDocument();
});

test("confirming the answers declares them and moves on to the questions", async () => {
  conflicts("launch", {
    complete: true,
    paragraph: "I confirm I have no conflict.",
    questions: state().questions.map((q) => ({ ...q, answer: "no" as const })),
  });
  let declared = 0;
  server.use(
    http.post("/v1/cases/:caseId/conflicts/declare", () => {
      declared += 1;
      return json({ declared_at: "2026-10-17T09:00:00Z", outcome: "none", note: note("conflict_declared") });
    }),
    http.get("/v1/cases/:caseId/questions", () => json({ questions: [], assumptions: [], locked: false, reading: true, reopened: false, rejected: 0, warnings: [], version_no: 1, instruction_version_id: null })),
  );
  const { user, router } = renderAt(`/cases/${CASE_A}/conflicts`);
  await user.click(await screen.findByRole("button", { name: "Confirm and continue" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_A}/questions`));
  expect(declared).toBe(1);
});

test("declaring something new mid-case gives a note for whoever instructed you", async () => {
  conflicts("launch", {
    complete: true,
    paragraph: "I disclose the following. I met them once in 2019.",
    declared_at: "2026-10-05T09:00:00Z",
    outcome: "none",
    questions: state().questions.map((q, i) => ({ ...q, answer: i === 0 ? ("yes" as const) : ("no" as const), detail: i === 0 ? "I met them once in 2019." : null })),
  });
  server.use(
    http.post("/v1/cases/:caseId/conflicts/declare", () =>
      json({ declared_at: "2026-10-17T09:00:00Z", outcome: "disclosed", note: note("conflict_declared") }),
    ),
  );
  const { user } = renderAt(`/cases/${CASE_A}/conflicts`);
  await user.click(await screen.findByRole("button", { name: "Declare this change" }));
  const drafted = await screen.findByRole("region", { name: "A note to send" });
  expect(within(drafted).getByText("Something I need to tell you about this case")).toBeInTheDocument();
  expect(within(drafted).getByText("Ms Rees, Rees & Co")).toBeInTheDocument();
});

test("the note names each declared matter by the question's own words", async () => {
  conflicts("launch", {
    complete: true,
    paragraph: "I disclose the following. I met them once in 2019.",
    declared_at: "2026-10-05T09:00:00Z",
    outcome: "none",
  });
  const item = { question_id: null, number: null, para_ref: null, label: "Have you met the person?", label_key: null, text: "I met them once in 2019." };
  server.use(
    http.post("/v1/cases/:caseId/conflicts/declare", () =>
      json({ declared_at: "2026-10-17T09:00:00Z", outcome: "disclosed", note: note("conflict_declared", { items: [item] as Schemas["NoteDraft"]["items"] }) }),
    ),
  );
  const { user } = renderAt(`/cases/${CASE_A}/conflicts`);
  await user.click(await screen.findByRole("button", { name: "Declare this change" }));
  const drafted = await screen.findByRole("region", { name: "A note to send" });
  expect(within(drafted).getByText("Have you met the person?: I met them once in 2019.")).toBeInTheDocument();
});

test("stepping back asks for the code, then records the reason", async () => {
  conflicts("launch");
  let stepped = false;
  let sent: unknown;
  server.use(
    http.post("/v1/cases/:caseId/withdraw", async ({ request }) => {
      if (!stepped) return apiError(403, "step_up_required");
      sent = await request.json();
      return json({ status: "withdrawn", deadlines_cancelled: 0, note: note("withdrawal") });
    }),
    http.post("/v1/auth/mfa/verify", () => {
      stepped = true;
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/conflicts`);
  await user.click(await screen.findByRole("button", { name: "Step back from this case" }));
  const dialog = await screen.findByRole("dialog", { name: "Step back from this case" });
  await user.type(within(dialog).getByLabelText("Why are you stepping back?"), "I know the family.");
  await user.click(within(dialog).getByRole("button", { name: "Step back" }));
  const code = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(code).getByLabelText("6-digit code"), "654321");
  await user.click(within(code).getByRole("button", { name: "Carry on" }));
  await waitFor(() => expect(sent).toEqual({ reason: "I know the family." }));
  expect(await screen.findByText("You've stepped back from this case.")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "A note to send" })).toBeInTheDocument();
});
