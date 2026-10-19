// S06 as drawn on the design board: the letter's cited paragraphs beside the questions, the kind as a
// coloured pill, Q chips, the outside-expertise card with "answer the timing only", and the footer.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { assumption, casePack, inCase, launchWords, question, questions, type PackRun } from "./testing";

type Over = Parameters<typeof questions>[0];

function asked(run: PackRun, over: Over = {}) {
  inCase(run);
  const kind = String(casePack(run).question_types[0]!.key);
  const body = questions({ questions: [question({ kind })], ...over });
  server.use(http.get("/v1/cases/:caseId/questions", () => json(body)));
  return body;
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the letter pane shows the cited paragraphs, and each card has a Q chip and its kind as a pill", async () => {
    asked(run);
    const { container } = renderAt(`/cases/${CASE_A}/questions`);
    const letter = await screen.findByRole("region", { name: "Letter of instruction" });
    expect(within(letter).getByText("4.1")).toBeInTheDocument();
    expect(within(letter).getByText(question().text_verbatim).tagName).toBe("MARK");
    expect(within(letter).getByText(assumption().text_verbatim).tagName).toBe("MARK");
    const card = await screen.findByRole("article", { name: "Question 1" });
    expect(within(card).getByText("Q1")).toBeInTheDocument();
    const first = casePack(run).question_types[0]!;
    expect(within(card).getByTestId("kind-pill")).toHaveTextContent(new RegExp(String(first.key).replace(/[_-]/g, ".?"), "i"));
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("a question outside your expertise explains it and offers to answer only the parts left to you", async () => {
  asked("launch", {
    questions: [question({ kind: "causation", outside_expertise: true, outside_expertise_source: "pack", answer_parts: ["timing"] })],
  });
  let sent: unknown;
  server.use(
    http.patch("/v1/cases/:caseId/questions/:id", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json(question({ kind: "causation", status: "confirmed", outside_expertise: true, outside_expertise_source: "pack", answer_parts: ["timing"], version: 2 }), { headers: { etag: '"2"' } });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  expect(within(card).getByText("This may be outside your expertise.")).toBeInTheDocument();
  await user.click(within(card).getByRole("button", { name: "Answer timing only" }));
  await waitFor(() => expect(sent).toEqual({ confirm: true }));
  expect(await within(card).findByText("Confirmed")).toBeInTheDocument();
});

test("a question the expert marked says so", async () => {
  asked("launch", { questions: [question({ outside_expertise: true, outside_expertise_source: "expert", answer_parts: ["timing"] })] });
  renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  expect(within(card).getByText("You marked this question as outside your expertise.")).toBeInTheDocument();
});

test("the footer says how many are left, links to adding a further letter, and confirms the set", async () => {
  asked("launch", { questions: [question({ status: "confirmed" }), question({ id: "a2222222-2222-4222-8222-222222222222", number: 2, status: "suggested" })] });
  renderAt(`/cases/${CASE_A}/questions`);
  expect(await screen.findByText(/Confirm the last 2 to continue/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Got a further letter or phone call? Add it" })).toHaveAttribute("href", `/cases/${CASE_A}/details`);
  expect(screen.getByRole("button", { name: "Confirm questions" })).toBeInTheDocument();
});
