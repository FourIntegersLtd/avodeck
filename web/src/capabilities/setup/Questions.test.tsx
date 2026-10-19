// S06 What you've been asked: the letter's own words for every question and assumption. An edit keeps the
// letter's words on show, a question can be flagged as outside the expert's field (with a note to draft),
// everything can be confirmed at once, and a further letter reopens the questions with a warning.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { assumption, casePack, inCase, launchWords, note, question, questions, type PackRun } from "./testing";

type Over = Parameters<typeof questions>[0];

function asked(run: PackRun, over: Over = {}) {
  inCase(run);
  const kind = String(casePack(run).question_types[0]!.key);
  const body = questions({ questions: [question({ kind })], ...over });
  server.use(http.get("/v1/cases/:caseId/questions", () => json(body)));
  return body;
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows each question in the letter's own words with where it came from, and passes axe", async () => {
    asked(run);
    const { container } = renderAt(`/cases/${CASE_A}/questions`);
    expect(await screen.findByRole("heading", { level: 1, name: "What you've been asked" })).toBeInTheDocument();
    const card = await screen.findByRole("article", { name: "Question 1" });
    expect(within(card).getByText(question().text_verbatim)).toBeInTheDocument();
    expect(within(card).getByText("From paragraph 4.1")).toBeInTheDocument();
    await userEvent.setup().click(within(card).getByRole("button", { name: "Edit wording" }));
    const kinds = within(card).getByLabelText("What kind of question is this?");
    const options = within(kinds).getAllByRole("option").map((o) => o.textContent);
    expect(options).toHaveLength(casePack(run).question_types.length);
    expect(screen.getByRole("article", { name: "Assumption 1" })).toBeInTheDocument();
    expect(screen.getByText("0 of 2 confirmed")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("confirming a question sends the version you saw", async () => {
  asked("launch");
  let sent: { body: unknown; ifMatch: string | null } | undefined;
  server.use(
    http.patch("/v1/cases/:caseId/questions/:id", async ({ request }) => {
      sent = { body: await request.json(), ifMatch: request.headers.get("if-match") };
      return HttpResponse.json(question({ status: "confirmed", version: 2 }), { headers: { etag: '"2"' } });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  await user.click(within(card).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(sent).toEqual({ body: { confirm: true }, ifMatch: '"1"' }));
  expect(await within(card).findByText("Confirmed")).toBeInTheDocument();
});

test("changing the wording keeps the letter's own words on show", async () => {
  asked("launch");
  let sent: unknown;
  server.use(
    http.patch("/v1/cases/:caseId/questions/:id", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json(question({ confirmed_text: "Were the February results acted on?", version: 2 }), { headers: { etag: '"2"' } });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  await user.click(within(card).getByRole("button", { name: "Edit wording" }));
  const box = within(card).getByLabelText("Your wording");
  await user.clear(box);
  await user.type(box, "Were the February results acted on?");
  await user.click(within(card).getByRole("button", { name: "Save the wording" }));
  await waitFor(() => expect(sent).toEqual({ text: "Were the February results acted on?" }));
  expect(await within(card).findByText("Were the February results acted on?")).toBeInTheDocument();
  expect(within(card).getByText("The letter says:")).toBeInTheDocument();
  expect(within(card).getByText(question().text_verbatim)).toBeInTheDocument();
});

test("a question outside your field can be flagged, and a note drafted about it", async () => {
  asked("launch");
  let flagged: unknown;
  let drafted: unknown;
  server.use(
    http.patch("/v1/cases/:caseId/questions/:id", async ({ request }) => {
      flagged = await request.json();
      return HttpResponse.json(question({ outside_expertise: true, version: 2 }), { headers: { etag: '"2"' } });
    }),
    http.post("/v1/cases/:caseId/instructing-party-notes/draft", async ({ request }) => {
      drafted = await request.json();
      return json(note("outside_expertise", { items: [{ question_id: question().id, number: 1, para_ref: "4.1", text: question().text_verbatim, label_key: null }] }));
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  await user.click(within(card).getByRole("button", { name: "Edit wording" }));
  await user.click(within(card).getByLabelText("This may be outside my expertise"));
  await waitFor(() => expect(flagged).toEqual({ outside_expertise: true }));
  await user.click(await within(card).findByRole("button", { name: `Draft a note to the ${casePack("launch").instructing_party_types[0]!.label}` }));
  await waitFor(() => expect(drafted).toEqual({ topic: "outside_expertise", question_ids: [question().id] }));
  const draft = await screen.findByRole("region", { name: "A note to send" });
  expect(within(draft).getByText("A question outside my expertise")).toBeInTheDocument();
});

test("confirm questions locks the set once every question and assumption is confirmed", async () => {
  asked("launch", { questions: [question({ status: "confirmed" })], assumptions: [assumption({ status: "confirmed" })] });
  let confirmed = 0;
  server.use(
    http.post("/v1/cases/:caseId/questions/confirm-all", () => {
      confirmed += 1;
      server.use(
        http.get("/v1/cases/:caseId/questions", () =>
          json(questions({ locked: true, questions: [question({ status: "confirmed" })], assumptions: [assumption({ status: "confirmed" })] })),
        ),
      );
      return json({ confirmed: 2, locked: true });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  await user.click(await screen.findByRole("button", { name: "Confirm questions" }));
  await waitFor(() => expect(confirmed).toBe(1));
  expect(await screen.findByText("You've confirmed what you've been asked.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm questions" })).toBeNull();
});

test("a further letter reopens the questions with a warning", async () => {
  asked("launch", { reopened: true, warnings: ["setup.questions.warning.reopened"] });
  renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByText("A further letter has changed what you've been asked. Please check the questions again.")).toBeInTheDocument();
});

test("while the letter is being read the screen says so", async () => {
  asked("launch", { reading: true, questions: [], assumptions: [] });
  renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByText("We're still reading the letter. This page will update by itself.")).toBeInTheDocument();
});

test("confirm questions stays off until every question and assumption is confirmed, and says how many are left", async () => {
  asked("launch");
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const button = await screen.findByRole("button", { name: "Confirm questions" });
  expect(button).toBeDisabled();
  expect(screen.getByText(/Confirm the last 2 to continue/)).toBeInTheDocument();
  server.use(
    http.patch("/v1/cases/:caseId/questions/:id", () => HttpResponse.json(question({ status: "confirmed", version: 2 }), { headers: { etag: '"2"' } })),
    http.patch("/v1/cases/:caseId/assumptions/:id", () => HttpResponse.json(assumption({ status: "confirmed", version: 2 }), { headers: { etag: '"2"' } })),
  );
  await user.click(within(screen.getByRole("article", { name: "Question 1" })).getByRole("button", { name: "Confirm" }));
  await user.click(within(screen.getByRole("article", { name: "Assumption 1" })).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm questions" })).toBeEnabled());
});
