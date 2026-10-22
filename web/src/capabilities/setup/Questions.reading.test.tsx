// S06 and S05 after review: a reading of the letter that failed says so and can be tried again, a letter
// added without its file asks for the letter's own words, and changing an answer after declaring asks the
// expert to declare again.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { inCase, questions } from "./testing";

test("a failed reading says so and the letter can be read again", async () => {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/questions", () =>
      json(questions({ questions: [], assumptions: [], read_failed: true, warnings: ["setup.questions.warning.read_failed"] })),
    ),
  );
  let again = 0;
  server.use(
    http.post("/v1/cases/:caseId/questions/read-again", () => {
      again += 1;
      return json({ job_id: "00000000-0000-4000-8000-000000000001" }, 202);
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByText("We couldn't read the letter. Please try again.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Read the letter again" }));
  await waitFor(() => expect(again).toBe(1));
});

test("a letter added without its file asks for it", async () => {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/questions", () =>
      json(questions({ questions: [], assumptions: [], warnings: ["setup.questions.warning.letter_without_file"] })),
    ),
  );
  renderAt(`/cases/${CASE_A}/questions`);
  expect(
    await screen.findByText("A further letter was added without its file. Please add the letter so we can find what you've been asked."),
  ).toBeInTheDocument();
});

test("an answer changed after declaring asks the expert to declare again", async () => {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/conflicts", () =>
      json({
        questions: [{ key: "knows_people", text: "Do you know any of the people in this case?", detail_required: true, answer: "no", detail: null }],
        complete: true,
        paragraph: "I have nothing to declare.",
        coverage: { status: "covered", period_start: "2024-02-01", period_end: "2024-08-31" },
        other_cases: { checked: 0, matches: 0, organisations: [] },
        warnings: ["setup.conflicts.warning.declare_again"],
        declared_at: "2026-10-17T09:00:00Z",
        outcome: "none",
        changed_since_declared: true,
      }),
    ),
  );
  renderAt(`/cases/${CASE_A}/conflicts`);
  expect(await screen.findByText("You've changed an answer since you confirmed them. Please confirm your answers again.")).toBeInTheDocument();
});
