// S14 Points against: the facts on each side (labels from the pack), unused facts, relevant-fact
// suggestions, accounts that differ, the range note and the way on to S15.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { claim, FACT_2, ok, pointFact, pointOut, pointsBody, progress, Q1, Q2, serve, sides, type PackRun } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/points`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows a column for each side from the pack, the point's page, and passes axe", async () => {
    serve(run);
    const { container } = renderAt(`${url}?q=${Q1}`);
    expect(await screen.findByRole("heading", { level: 2, name: M["write.points.title"] })).toBeInTheDocument();
    for (const side of sides(run)) expect(await screen.findByRole("heading", { level: 3, name: side.label })).toBeInTheDocument();
    const column = screen.getByRole("region", { name: sides(run)[0]!.label });
    expect(within(column).getByText("Bleeding for 3 weeks")).toBeInTheDocument();
    expect(within(column).getByText("p.118")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the question tabs list only questions the expert has confirmed, and the first is open by default", async () => {
    serve(run);
    renderAt(url);
    const tabs = await screen.findAllByRole("link", { name: /Question \d/ });
    expect(tabs.map((tab) => tab.textContent)).toEqual([expect.stringContaining("Question 1"), expect.stringContaining("Question 2")]);
    expect(tabs[0]).toHaveAttribute("aria-current", "page");
    expect(tabs[1]).toHaveAttribute("href", `/cases/${CASE_A}/points?q=${Q2}`);
  });
});

test("moving a point sends the side and the version you saw", async () => {
  const { sent, record } = serve("launch");
  server.use(
    http.patch("/v1/cases/:caseId/points/:id", async ({ request }) => {
      await record(request);
      return ok(pointOut({ side: "supports_clinician", version: 2 }), 2);
    }),
  );
  const { user } = renderAt(`${url}?q=${Q1}`);
  const column = await screen.findByRole("region", { name: sides("launch")[0]!.label });
  await user.click(within(column).getByRole("button", { name: `Move to ${sides("launch")[1]!.label}` }));
  await waitFor(() => expect(sent).toContainEqual({ method: "PATCH", path: expect.stringMatching(/\/points\//), body: { side: "supports_clinician" }, ifMatch: '"1"' }));
});

test("keeping a point accepts it", async () => {
  const { sent, record } = serve("launch");
  server.use(http.patch("/v1/cases/:caseId/points/:id", async ({ request }) => (await record(request), ok(pointOut({ status: "accepted", version: 2 }), 2))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  await user.click(await screen.findByRole("button", { name: M["write.points.keep"]! }));
  await waitFor(() => expect(sent.at(-1)?.body).toEqual({ status: "accepted" }));
});

test("removing a point asks for a short reason first", async () => {
  const { sent, record } = serve("launch");
  server.use(http.patch("/v1/cases/:caseId/points/:id", async ({ request }) => (await record(request), ok(pointOut({ status: "dismissed", version: 2 }), 2))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  await user.click(await screen.findByRole("button", { name: M["write.points.remove"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["write.points.remove_title"] });
  await user.click(within(dialog).getByRole("button", { name: M["write.points.remove_confirm"]! }));
  expect(sent).toEqual([]);
  await user.type(within(dialog).getByLabelText(M["write.points.remove_reason"]!), "Not relevant");
  await user.click(within(dialog).getByRole("button", { name: M["write.points.remove_confirm"]! }));
  await waitFor(() => expect(sent.at(-1)?.body).toEqual({ status: "dismissed", dismissed_reason: "Not relevant" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("an unused fact is added to the side the expert picks", async () => {
  const { sent, record } = serve("launch");
  server.use(http.post("/v1/cases/:caseId/questions/:q/points", async ({ request }) => (await record(request), json(pointOut({ id: "b2222222-2222-4222-8222-222222222222", status: "accepted", side: "supports_clinician", fact: pointFact({ id: FACT_2, value: "Iron deficiency on file" }) }), 201))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  // Unused facts sit behind the board's "Add another fact… + Add" row on the side's card.
  const addRow = M["write.points.add_row_button"];
  expect(addRow).toBeTruthy();
  await screen.findByRole("heading", { name: sides("launch")[1]!.label });
  expect(screen.queryByRole("region", { name: M["write.points.unused"] })).not.toBeInTheDocument();
  await user.click((await screen.findAllByRole("button", { name: addRow! }))[1]!);
  const unused = await screen.findByRole("region", { name: M["write.points.unused"] });
  await user.click(within(unused).getByRole("button", { name: `Add to ${sides("launch")[1]!.label}` }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", body: { fact_id: FACT_2, side: "supports_clinician" } }));
  expect(await screen.findAllByText("Iron deficiency on file")).toHaveLength(1);
});

test("a relevant fact can be dismissed", async () => {
  const relevant = { id: "d2222222-2222-4222-8222-222222222222", status: "suggested", version: 3, why_relevant: "mentions pain", fact: pointFact({ id: FACT_2, value: "Out-of-hours call" }) };
  const { sent, record } = serve("launch", { points: pointsBody("launch", { relevant: [relevant] }) });
  server.use(http.patch("/v1/cases/:caseId/relevant-facts/:id", async ({ request }) => (await record(request), ok({ ...relevant, status: "dismissed", version: 4 }, 4))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  expect(await screen.findByText("You haven't used 1 relevant fact yet.")).toBeInTheDocument();
  expect(screen.getByText("mentions pain")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["write.points.relevant_dismiss"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { status: "dismissed" }, ifMatch: '"3"' }));
  await waitFor(() => expect(screen.queryByText("mentions pain")).not.toBeInTheDocument());
});

test("an account that differs shows the statement and the record side by side and can be confirmed", async () => {
  const { sent, record } = serve("launch", { points: pointsBody("launch", { claims: [claim()] }) });
  server.use(http.patch("/v1/cases/:caseId/witness-claims/:id", async ({ request }) => (await record(request), ok(claim({ status: "confirmed", version: 2 }), 2))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  const differ = await screen.findByRole("region", { name: M["write.points.differ"] });
  expect(within(differ).getByText("she told the doctor she had lost weight")).toBeInTheDocument();
  expect(within(differ).getByText("Weight loss first mentioned on 8 August")).toBeInTheDocument();
  expect(within(differ).getByText(M["write.points.judge"]!)).toBeInTheDocument();
  await user.click(within(differ).getByRole("button", { name: M["write.points.claim_confirm"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { status: "confirmed" }, ifMatch: '"1"' }));
  await waitFor(() => expect(screen.queryByText("Weight loss first mentioned on 8 August")).not.toBeInTheDocument());
});

test("the range note uses the pack's prompt and saves with its version", async () => {
  const { sent, record } = serve("launch");
  server.use(http.put("/v1/cases/:caseId/questions/:q/range-note", async ({ request }) => (await record(request), ok({ prompt: "x", text: "My note", version: 1 }, 1))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  const box = await screen.findByLabelText(pointsBody("launch").range_note.prompt);
  await user.type(box, "My note");
  await user.click(screen.getByRole("button", { name: M["write.points.range_save"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PUT", body: { text: "My note" }, ifMatch: '"0"' }));
  expect(await screen.findByText(M["write.points.range_saved"]!)).toBeInTheDocument();
});

test("Write my opinion goes to the opinion screen for this question", async () => {
  serve("launch");
  renderAt(`${url}?q=${Q2}`);
  expect(await screen.findByRole("link", { name: M["write.points.write"]! })).toHaveAttribute("href", `/cases/${CASE_A}/opinion?q=${Q2}`);
});

test("a point whose fact changed says so, and Keep accepts it again with the version you saw", async () => {
  const stale = pointOut({ status: "stale", origin: "expert", version: 3 });
  const { sent, record } = serve("launch", { points: pointsBody("launch", { points: [stale] }) });
  server.use(http.patch("/v1/cases/:caseId/points/:id", async ({ request }) => (await record(request), ok(pointOut({ status: "accepted", version: 4 }), 4))));
  const { user } = renderAt(`${url}?q=${Q1}`);
  expect(await screen.findByText(M["write.points.stale_point"]!)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["write.points.keep"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { status: "accepted" }, ifMatch: '"3"' }));
  await waitFor(() => expect(screen.queryByText(M["write.points.stale_point"]!)).not.toBeInTheDocument());
});

test("each account shows its label (the expert's own over the AI's) and says when the record changed", async () => {
  const second = claim({ id: "c2222222-2222-4222-8222-222222222222", claim_index: 1, text_verbatim: "second claim", verdict: "supports", expert_verdict: "not_addressed", status: "stale", facts: [] });
  serve("launch", { points: pointsBody("launch", { claims: [claim(), second] }) });
  renderAt(`${url}?q=${Q1}`);
  const differ = await screen.findByRole("region", { name: M["write.points.differ"] });
  const [first, other] = within(differ).getAllByRole("article");
  expect(within(first!).getByText(M["write.points.verdict.contradicts"]!)).toBeInTheDocument();
  expect(within(first!).queryByText(M["write.points.claim_stale"]!)).not.toBeInTheDocument();
  expect(within(other!).getByText(M["write.points.verdict.not_addressed"]!)).toBeInTheDocument();
  expect(within(other!).queryByText(M["write.points.verdict.supports"]!)).not.toBeInTheDocument();
  expect(within(other!).getByText(M["write.points.claim_stale"]!)).toBeInTheDocument();
});

test("the screens' own words name no court or judge (those come from the packs)", () => {
  expect(Object.values(M).join(" ")).not.toMatch(/\b(court|judge)s?\b/i);
});

test("a question tab says when its points are done", async () => {
  serve("launch");
  renderAt(`${url}?q=${Q2}`);
  const tabs = await screen.findAllByRole("link", { name: /Question \d/ });
  expect(within(tabs[0]!).getByText(M["write.points.tab_done"]!)).toBeInTheDocument();
  expect(within(tabs[1]!).queryByText(M["write.points.tab_done"]!)).not.toBeInTheDocument();
});

test("the done line shows only once this question's points are settled", async () => {
  serve("launch");
  const first = renderAt(`${url}?q=${Q1}`);
  expect(await screen.findByText("Done for question 1. You can now write your opinion.")).toBeInTheDocument();
  first.unmount();
  serve("launch");
  renderAt(`${url}?q=${Q2}`);
  await screen.findByRole("region", { name: sides("launch")[0]!.label });
  expect(screen.queryByText(/^Done for question/)).not.toBeInTheDocument();
});

test("a relevant fact the draft has not used links to the page as the clinician saw it", async () => {
  serve("launch", { points: pointsBody("launch", { relevant: [{ id: "d1111111-1111-4111-8111-111111111111", status: "suggested", why_relevant: "Mentions pain", version: 1, fact: pointFact({ id: FACT_2, value: "Out-of-hours call" }) }] }), progress: progress() });
  renderAt(`${url}?q=${Q1}`);
  expect(await screen.findByRole("link", { name: M["write.points.relevant_look"]! })).toHaveAttribute("href", `/cases/${CASE_A}/consultation/${FACT_2}`);
});

test("adding a fact that is already a point says so in plain words and shows that point", async () => {
  serve("launch");
  const existing = pointOut({ id: "b3333333-3333-4333-8333-333333333333", side: "supports_clinician", fact: pointFact({ id: FACT_2, value: "Iron deficiency on file" }) });
  let posted = false;
  server.use(
    http.post("/v1/cases/:caseId/questions/:q/points", () => ((posted = true), apiError(409, "conflict"))),
    http.get("/v1/cases/:caseId/questions/:q/points", () => json(pointsBody("launch", posted ? { points: [pointOut(), existing], unused: [] } : {}))),
  );
  const { user } = renderAt(`${url}?q=${Q1}`);
  await screen.findByRole("heading", { name: sides("launch")[1]!.label });
  await user.click((await screen.findAllByRole("button", { name: M["write.points.add_row_button"]! }))[1]!);
  const unused = await screen.findByRole("region", { name: M["write.points.unused"] });
  await user.click(within(unused).getByRole("button", { name: `Add to ${sides("launch")[1]!.label}` }));
  expect(await screen.findByText(M["write.points.exists"]!)).toBeInTheDocument();
  expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["write.points.exists_show"]! }));
  expect(document.getElementById(`point-${existing.id}`)).toHaveFocus();
});
