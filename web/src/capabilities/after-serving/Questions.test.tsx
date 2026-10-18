// S20 Questions from the other side: the rounds, importing a set, each question with its classification and
// answer (If-Match), telling the instructing party, and signing the answers behind the code.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { AFTER_MESSAGES as M } from "./words";
import { cap, declarations, DOC, partyName, question, questionsName, Q1, Q2, round, ROUND, serve, type PackRun } from "./testing";

const url = `/cases/${CASE_A}/written-questions`;
const suggestion = (run: PackRun) => (run === "launch" ? "clarifies" : "permitted");

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("is named by the pack, lists the question as it was written with a suggested classification, and passes axe", async () => {
    serve(run, { questions: [question({ suggested_classification: suggestion(run) })] });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: cap(questionsName(run)) })).toBeInTheDocument();
    expect(await screen.findByText("Would your opinion change if the examination was not recorded?")).toBeInTheDocument();
    expect(screen.getByText(run === "launch" ? M["after_serving.classification.clarifies"]! : "Permitted")).toBeInTheDocument();
    expect(screen.getByText(M["after_serving.questions.suggested"]!)).toBeInTheDocument();
    expect(screen.getByText("Set 1 · received 1 December 2026")).toBeInTheDocument();
    expect(screen.getByText("Answers due 26 December 2026")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("an answer is saved with the version the screen saw", async () => {
  const { sent, record } = serve("launch", { questions: [question({ version: 4 })] });
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", async ({ request }) => (await record(request), json(question({ answer_text: "My answer.", version: 5 })))));
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(M["after_serving.questions.answer"]!), "My answer.");
  await user.click(screen.getByRole("button", { name: M["after_serving.questions.answer_save"]! }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toMatchObject({ method: "PATCH", path: `/v1/cases/${CASE_A}/written-questions/${Q1}`, ifMatch: '"4"', body: { answer_text: "My answer." } });
  expect(await screen.findByText(M["after_serving.questions.saved"]!)).toBeInTheDocument();
});

test("the classification can be changed to another one the pack uses", async () => {
  const { sent, record } = serve("launch", { questions: [question({ suggested_classification: "clarifies" }), question({ id: Q2, number: 2, suggested_classification: "goes_beyond" })] });
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", async ({ request }) => (await record(request), json(question({ classification: "goes_beyond", version: 2 })))));
  const { user } = renderAt(url);
  const [first] = await screen.findAllByLabelText(M["after_serving.questions.classification"]!);
  await user.selectOptions(first!, "goes_beyond");
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toMatchObject({ method: "PATCH", path: expect.stringContaining(Q1), body: { classification: "goes_beyond" } });
});

test("when someone else changed the answer first, it asks which version to keep", async () => {
  serve("launch");
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", () => apiError(412, "stale")));
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(M["after_serving.questions.answer"]!), "Mine");
  await user.click(screen.getByRole("button", { name: M["after_serving.questions.answer_save"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["after_serving.stale_title"] });
  expect(within(dialog).getByRole("button", { name: M["after_serving.stale_mine"]! })).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: M["after_serving.stale_theirs"]! })).toBeInTheDocument();
});

test("telling the instructing party shows the note the server drafts, in the pack's word for them", async () => {
  const { sent, record } = serve("launch");
  server.use(http.post("/v1/cases/:caseId/written-questions/:q/tell-instructing-party", async ({ request }) => (await record(request), json({ key: "after_serving.questions.tell_note", params: { round: 1, number: 1, para_ref: "6.12" } }))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: `Tell the ${partyName("launch")}` }));
  expect(await screen.findByText("Question 1 in set 1 (paragraph 6.12) may go beyond clarifying my report. Please decide whether to raise it with the court.")).toBeInTheDocument();
  expect(sent[0]).toMatchObject({ method: "POST", path: expect.stringMatching(/tell-instructing-party$/) });
});

test("a set that hasn't been read can be imported, which queues the reading", async () => {
  const { sent, record } = serve("launch", { rounds: [round({ extracted_at: null })], questions: [] });
  server.use(http.post("/v1/cases/:caseId/written-questions/import", async ({ request }) => (await record(request), json({ job_id: "j1" }, 202))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.questions.read"]! }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", body: { round_id: ROUND } }));
  expect(await screen.findByText(M["after_serving.questions.reading"]!)).toBeInTheDocument();
});

test("a new set is added from the file just added, with the dates", async () => {
  const { sent, record } = serve("launch", { rounds: [], questions: [] });
  server.use(http.post("/v1/cases/:caseId/written-question-rounds", async ({ request }) => (await record(request), json(round(), 201))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  await user.type(await screen.findByLabelText(M["after_serving.questions.received_on"]!), "2026-12-01");
  await user.type(screen.getByLabelText(M["after_serving.questions.answers_due_on"]!), "2026-12-26");
  await user.click(screen.getByRole("button", { name: M["after_serving.questions.add_submit"]! }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", body: { document_id: DOC, received_on: "2026-12-01", answers_due_on: "2026-12-26" } }));
});

test("without a file to add, the add button is off and says why", async () => {
  serve("launch", { rounds: [], questions: [] });
  renderAt(url);
  expect(await screen.findByText(M["after_serving.questions.document_needed"]!)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: M["after_serving.questions.add_submit"]! })).toBeDisabled();
});

test("signing the answers shows the declaration, needs the box ticked, asks for the code, then shows them locked", async () => {
  let stepped = false;
  let signed = false;
  const bodies: unknown[] = [];
  serve("launch", { questions: [question({ answer_text: "Done", status: "answering" })] });
  server.use(
    http.get("/v1/cases/:caseId/written-question-rounds", () => json({ rounds: [round({ status: signed ? "answered" : "open", answers_version_id: signed ? "v9" : null })] })),
    http.post("/v1/cases/:caseId/written-questions/answers/sign", async ({ request }) => {
      if (!stepped) return apiError(403, "step_up_required");
      bodies.push(await request.json());
      signed = true;
      return json({ version: { id: "v9", display_name: "Answers", status: "locked", type: "answers", version_no: 2, locked_at: "2026-12-20T10:00:00Z", prev_version_id: null, sha256: "a" } });
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user } = renderAt(url);
  for (const d of declarations("launch")) for (const part of d.text.split("\n\n")) expect(await screen.findByText(part)).toBeInTheDocument();
  const button = screen.getByRole("button", { name: M["after_serving.questions.sign"]! });
  expect(button).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: M["after_serving.questions.confirm"]! }));
  await user.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "123456");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  expect(await screen.findByText(M["after_serving.questions.signed"]!)).toBeInTheDocument();
  expect(bodies).toEqual([{ declaration_confirmed: true, round_id: ROUND }]);
  expect(screen.queryByRole("button", { name: M["after_serving.questions.sign"]! })).toBeNull();
});

test.each([["answers_missing"], ["already_signed"], ["locked"]])("a refused signing (%s) is explained in plain words", async (code) => {
  serve("launch");
  server.use(http.post("/v1/cases/:caseId/written-questions/answers/sign", () => apiError(409, code)));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("checkbox", { name: M["after_serving.questions.confirm"]! }));
  await user.click(screen.getByRole("button", { name: M["after_serving.questions.sign"]! }));
  expect(await screen.findByText(M[`after_serving.questions.error.${code}`]!)).toBeInTheDocument();
});

test("Today's items open these screens", async () => {
  const { signedIn } = await import("../../test/api");
  const { today, caseCard } = await import("../../test/fixtures");
  signedIn({ today: today({ cases: [caseCard()], needs_you: [
    { kind: "after_serving_questions_due", case_id: CASE_A, rank: 10, label_key: "after_serving.today.questions_due", action_key: "after_serving.today.answer_questions", screen: "S20", count: 2 },
    { kind: "after_serving_new_pages", case_id: CASE_A, rank: 11, label_key: "after_serving.today.new_pages", action_key: "after_serving.today.read_new_pages", screen: "S21", count: 14 },
    { kind: "after_serving_opposing", case_id: CASE_A, rank: 12, label_key: "after_serving.today.opposing_to_check", action_key: "after_serving.today.check_opposing", screen: "S21", count: 3 },
  ] }) });
  renderAt("/today");
  expect(await screen.findByText("Mrs A: 2 questions to answer")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["after_serving.today.answer_questions"]! })).toHaveAttribute("href", `/cases/${CASE_A}/written-questions`);
  expect(screen.getByText("Mrs A: 14 new pages to read")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["after_serving.today.read_new_pages"]! })).toHaveAttribute("href", `/cases/${CASE_A}/new-material`);
  expect(screen.getByText("Mrs A: 3 statements in the other expert's report to check")).toBeInTheDocument();
});
