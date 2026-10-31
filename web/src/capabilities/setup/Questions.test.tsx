// S06 What you've been asked: the letter's own words for every question and assumption. An edit keeps the
// letter's words on show, a question can be flagged as outside the expert's field (with a note to draft),
// everything can be confirmed at once, and a further letter reopens the questions with a warning.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { assumption, casePack, inCase, launchWords, note, nowAsked, question, questions, type PackRun } from "./testing";

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:letter"), revokeObjectURL: vi.fn() }));
});

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
      const next = question({ status: "confirmed", version: 2 });
      nowAsked({ questions: [next] });
      return HttpResponse.json(next, { headers: { etag: '"2"' } });
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
      const next = question({ confirmed_text: "Were the February results acted on?", version: 2 });
      nowAsked({ questions: [next] });
      return HttpResponse.json(next, { headers: { etag: '"2"' } });
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
      const next = question({ outside_expertise: true, version: 2 });
      nowAsked({ questions: [next] });
      return HttpResponse.json(next, { headers: { etag: '"2"' } });
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
    http.patch("/v1/cases/:caseId/questions/:id", () => {
      const next = question({ status: "confirmed", version: 2 });
      nowAsked({ questions: [next] });
      return HttpResponse.json(next, { headers: { etag: '"2"' } });
    }),
    http.patch("/v1/cases/:caseId/assumptions/:id", () => {
      const next = assumption({ status: "confirmed", version: 2 });
      nowAsked({ questions: [question({ status: "confirmed", version: 2 })], assumptions: [next] });
      return HttpResponse.json(next, { headers: { etag: '"2"' } });
    }),
  );
  await user.click(within(screen.getByRole("article", { name: "Question 1" })).getByRole("button", { name: "Confirm" }));
  await user.click(within(screen.getByRole("article", { name: "Assumption 1" })).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm questions" })).toBeEnabled());
});


test("the letter pages show in the pane", async () => {
  asked("launch");
  server.use(
    http.get("/v1/cases/:caseId/instructions/:versionId/pages", () => json({ page_count: 2 })),
    http.get("/v1/cases/:caseId/instructions/:versionId/pages/:n", () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { "content-type": "image/png" } })),
  );
  renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByRole("img", { name: "Page 1" })).toBeInTheDocument();
  expect(await screen.findByRole("img", { name: "Page 2" })).toBeInTheDocument();
});

test("the add-question form adds a card", async () => {
  asked("launch", { questions: [], assumptions: [] });
  let sent: unknown;
  const added = question({ id: "d1111111-1111-4111-8111-111111111111", text_verbatim: "Whether the referral was timely.", para_ref: "4(a)", kind: casePack("launch").question_types[1]!.key as string });
  server.use(
    http.post("/v1/cases/:caseId/questions", async ({ request }) => {
      sent = await request.json();
      server.use(http.get("/v1/cases/:caseId/questions", () => json(questions({ questions: [added], assumptions: [], version_no: 2 }))));
      return json(added, 201);
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  await user.click(await screen.findByRole("button", { name: "Add a question we missed" }));
  await user.type(screen.getByLabelText("The question in the letter's words"), "Whether the referral was timely.");
  await user.type(screen.getByLabelText("Paragraph reference (e.g. 4(a))"), "4(a)");
  await user.selectOptions(screen.getByLabelText("What kind of question is this?"), String(casePack("launch").question_types[1]!.key));
  await user.click(screen.getByRole("button", { name: "Add question" }));
  await waitFor(() => expect(sent).toEqual({ text: "Whether the referral was timely.", para_ref: "4(a)", kind: casePack("launch").question_types[1]!.key }));
  expect(await screen.findByText("Whether the referral was timely.")).toBeInTheDocument();
});

test("zero questions shows the empty state and the add button", async () => {
  asked("launch", { questions: [], assumptions: [] });
  renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByText("We didn't find any questions in this letter. Read the letter and add them yourself.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add a question we missed" })).toBeVisible();
});

test("the add button is hidden when locked", async () => {
  asked("launch", { locked: true });
  renderAt(`/cases/${CASE_A}/questions`);
  await screen.findByRole("article", { name: "Question 1" });
  expect(screen.queryByRole("button", { name: "Add a question we missed" })).not.toBeInTheDocument();
});
