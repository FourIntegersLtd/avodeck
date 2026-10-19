// S15 Your opinion: fact chips that insert a citation marker, the expert's own words, the view and the
// test they apply, checks on what they wrote, autosave with a version, the two-windows prompt and versions.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { chip, FACT_1, lintIssue, ok, opinionOut, PAGE_1, progress, Q1, serve, views, workspace, type PackRun } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/opinion?q=${Q1}`;
afterEach(() => vi.useRealTimers());

function save(sent: ReturnType<typeof serve>["sent"], record: ReturnType<typeof serve>["record"], version = 2) {
  server.use(http.put("/v1/cases/:caseId/questions/:q/opinion", async ({ request }) => {
    await record(request);
    const body = sent.at(-1)!.body as { body: string; citation_refs: [] };
    return ok(opinionOut({ body: body.body, citation_refs: body.citation_refs, version }), version);
  }));
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows the facts, the day's guidance, the points summary, the views from the pack, and passes axe", async () => {
    serve(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: M["write.opinion.title"] })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "The facts for question 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bleeding for 3 weeks.*p\.118/ })).toBeInTheDocument();
    expect(screen.getByText("Guidance one")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: M["write.opinion.points_open"]! })).toHaveAttribute("href", `/cases/${CASE_A}/points?q=${Q1}`);
    for (const v of views(run)) expect(screen.getByRole("radio", { name: v.label })).toBeInTheDocument();
    expect(screen.getByText(M["write.opinion.yours_only"]!)).toBeInTheDocument();
    expect(screen.getByText(workspace(run).legal_test)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("the guidance card is hidden when no guidance is in force", async () => {
  serve("launch", { workspace: workspace("launch", { guidance: [], guidance_on: null }) });
  renderAt(url);
  await screen.findByRole("heading", { name: "The facts for question 1" });
  expect(screen.queryByText(/^Guidance on/)).not.toBeInTheDocument();
});

test("clicking a fact puts its marker in the text at the cursor and sends the server's own quote on the next save", async () => {
  const { sent, record } = serve("launch");
  save(sent, record);
  const { user } = renderAt(url);
  const box = await screen.findByLabelText(M["write.opinion.editor"]!);
  await user.type(box, "Before  after");
  (box as HTMLTextAreaElement).setSelectionRange(7, 7);
  await user.click(screen.getByRole("button", { name: /Bleeding for 3 weeks/ }));
  expect((box as HTMLTextAreaElement).value).toBe("Before [[cite:1]] after");
  await user.tab();
  await waitFor(() => expect(sent.at(-1)).toMatchObject({
    method: "PUT",
    ifMatch: '"0"',
    body: { body: "Before [[cite:1]] after", citation_refs: [{ fact_id: FACT_1, page_id: PAGE_1, quote_verbatim: chip().quote_verbatim }], view: null, provisional: false },
  }));
});

test("the view, the provisional flag and the test text are saved with the opinion", async () => {
  const { sent, record } = serve("launch");
  save(sent, record);
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("radio", { name: views("launch")[2]!.label }));
  await user.click(screen.getByRole("checkbox", { name: M["write.opinion.provisional"]! }));
  const test = screen.getByLabelText(M["write.opinion.test"]!);
  await user.type(test, "My wording of the test");
  await user.tab();
  await waitFor(() => expect(sent.at(-1)?.body).toMatchObject({ view: views("launch")[2]!.code, provisional: true, test_text: "My wording of the test" }));
});

test("it saves by itself five seconds after a change, then sends the new version", async () => {
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "Start", version: 4 }) }) });
  save(sent, record, 5);
  vi.useFakeTimers({ shouldAdvanceTime: true });
  renderAt(url);
  const box = await screen.findByLabelText(M["write.opinion.editor"]!);
  fireEvent.change(box, { target: { value: "Start more" } });
  expect(sent).toEqual([]);
  await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toMatchObject({ ifMatch: '"4"', body: { body: "Start more" } });
  fireEvent.change(box, { target: { value: "Start more again" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.ifMatch).toBe('"5"');
  expect(await screen.findByText(M["write.opinion.saved"]!)).toBeInTheDocument();
});

test("open checks are listed, and dismissing one asks for a reason", async () => {
  const issue = lintIssue();
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "x", lint: [issue] }) }) });
  server.use(http.patch("/v1/cases/:caseId/lint-issues/:id", async ({ request }) => (await record(request), json({ ...issue, status: "dismissed" }))));
  const { user } = renderAt(url);
  const panel = await screen.findByRole("region", { name: M["write.opinion.checks"] });
  expect(within(panel).getByText("A sentence states a fact without a page number: she had a lump")).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: M["write.opinion.issue_dismiss"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["write.opinion.dismiss_title"] });
  await user.type(within(dialog).getByLabelText(M["write.opinion.dismiss_reason"]!), "It is my own note");
  await user.click(within(dialog).getByRole("button", { name: M["write.opinion.dismiss_confirm"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", body: { dismissed_reason: "It is my own note" } }));
  await waitFor(() => expect(within(panel).queryByText(/she had a lump/)).not.toBeInTheDocument());
});

test("leaving the box asks for the checks again", async () => {
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "Old" }) }) });
  save(sent, record);
  server.use(http.post("/v1/cases/:caseId/questions/:q/opinion/lint", () => json({ issues: [lintIssue({ words: "a new thing" })] })));
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(M["write.opinion.editor"]!), " more");
  await user.tab();
  expect(await screen.findByText(/a new thing/)).toBeInTheDocument();
});

test("a second window's save asks which version to keep, then saves the choice with the new version", async () => {
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "Mine", version: 2 }) }) });
  let first = true;
  server.use(http.put("/v1/cases/:caseId/questions/:q/opinion", async ({ request }) => {
    await record(request);
    const body = sent.at(-1)!.body as { body: string };
    if (first) {
      first = false;
      return apiError(412, "stale", { yours: { ...body, citation_refs: [] }, saved: opinionOut({ body: "From the other window", version: 3 }) });
    }
    return ok(opinionOut({ body: body.body, version: 4 }), 4);
  }));
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(M["write.opinion.editor"]!), " edited");
  await user.tab();
  const dialog = await screen.findByRole("dialog", { name: M["write.opinion.stale_title"] });
  expect(within(dialog).getByText("From the other window")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: M["write.opinion.stale_yours"]! }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]).toMatchObject({ ifMatch: '"3"', body: { body: "Mine edited" } });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("earlier versions are listed and one can be restored", async () => {
  const opinion = opinionOut({ body: "Now", version: 3 });
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion }) });
  const version = (n: number, body: string) => ({ version_no: n, body, citation_refs: [], view: null, provisional: false, restored_from: null, saved_by: null, saved_at: `2026-10-1${n}T09:00:00Z` });
  server.use(
    http.get("/v1/cases/:caseId/opinions/:id/versions", () => json({ versions: [version(3, "Now"), version(2, "Earlier words")] })),
    http.post("/v1/cases/:caseId/opinions/:id/versions/:n/restore", async ({ request }) => (await record(request), ok(opinionOut({ body: "Earlier words", version: 4, restored_from: 2 }), 4))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["write.opinion.versions"]! }));
  const list = await screen.findByRole("region", { name: M["write.opinion.versions"] });
  expect(within(list).getByText("Earlier words")).toBeInTheDocument();
  await user.click(within(list).getByRole("button", { name: "Go back to version 2" }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: expect.stringMatching(/versions\/2\/restore$/), ifMatch: '"3"' }));
  expect((await screen.findByLabelText(M["write.opinion.editor"]!) as HTMLTextAreaElement).value).toBe("Earlier words");
});

test("Today's items open these screens", async () => {
  const { signedIn } = await import("../../test/api");
  const { today, caseCard } = await import("../../test/fixtures");
  signedIn({ today: today({ cases: [caseCard()], needs_you: [
    { kind: "write_questions_without_opinion", case_id: CASE_A, rank: 40, label_key: "write.today.questions_without_opinion", action_key: "write.today.questions_without_opinion_action", screen: "S15", count: 2 },
    { kind: "write_points_not_considered", case_id: CASE_A, rank: 41, label_key: "write.today.points_not_considered", action_key: "write.today.points_not_considered_action", screen: "S14", count: 1 },
  ] }) });
  renderAt("/today");
  expect(await screen.findByText("Mrs A: 2 questions without your opinion")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Write your opinion" })).toHaveAttribute("href", `/cases/${CASE_A}/opinion`);
  expect(screen.getByText("Mrs A: 1 point against not yet considered")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Look at the points" })).toHaveAttribute("href", `/cases/${CASE_A}/points`);
});

const REF_1 = { fact_id: FACT_1, page_id: PAGE_1, quote_verbatim: "bleeding for three weeks" };
const REF_2 = { fact_id: "f2222222-2222-4222-8222-222222222222", page_id: "92222222-2222-4222-8222-222222222222", quote_verbatim: "iron deficiency anaemia" };

test("a fact put in the text carries the fact's version as cited", async () => {
  const { sent, record } = serve("launch");
  save(sent, record);
  const { user } = renderAt(url);
  await screen.findByLabelText(M["write.opinion.editor"]!);
  await user.click(screen.getByRole("button", { name: /Bleeding for 3 weeks/ }));
  await user.tab();
  await waitFor(() => expect(sent.at(-1)?.body).toMatchObject({ citation_refs: [{ ...REF_1, fact_version: 2 }] }));
});

test("deleting a marker removes its citation and renumbers the rest before saving", async () => {
  const opinion = opinionOut({ body: "A [[cite:1]] B [[cite:2]].", citation_refs: [REF_1, REF_2], version: 2 });
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion }) });
  save(sent, record, 3);
  const { user } = renderAt(url);
  const box = await screen.findByLabelText(M["write.opinion.editor"]!);
  await user.click(box);
  fireEvent.change(box, { target: { value: "A  B [[cite:2]]." } });
  await user.tab();
  await waitFor(() => expect(sent.at(-1)?.body).toMatchObject({ body: "A  B [[cite:1]].", citation_refs: [REF_2] }));
  expect((box as HTMLTextAreaElement).value).toBe("A  B [[cite:1]].");
});

test("a refused citation stops the autosave, says which in plain words, and saves again after the next change", async () => {
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "Start [[cite:1]]", citation_refs: [REF_1], version: 4 }) }) });
  let refuse = true;
  server.use(http.put("/v1/cases/:caseId/questions/:q/opinion", async ({ request }) => {
    await record(request);
    if (refuse) return apiError(422, "invalid", { reason: "not_citable", citation: 1 });
    return ok(opinionOut({ body: "x", version: 5 }), 5);
  }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  renderAt(url);
  const box = await screen.findByLabelText(M["write.opinion.editor"]!);
  fireEvent.change(box, { target: { value: "Start more [[cite:1]]" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(await screen.findByText("Citation [1] doesn't match a fact you have checked. Remove it, or click the fact again to cite it as it is now.")).toBeInTheDocument();
  expect(screen.getByText(M["write.opinion.save_stopped"]!)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
  expect(sent).toHaveLength(1);
  refuse = false;
  fireEvent.change(box, { target: { value: "Start more again [[cite:1]]" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
  await waitFor(() => expect(sent).toHaveLength(2));
});

test("a citation whose fact changed since you cited it says so, and one click cites the fact as it is now", async () => {
  const old = { fact_id: FACT_1, page_id: "93333333-3333-4333-8333-333333333333", quote_verbatim: "bleeding for three weeks" };
  const opinion = opinionOut({ body: "See [[cite:1]].", citation_refs: [old], changed_citations: [1], stale: true, version: 2 });
  const { sent, record } = serve("launch", { workspace: workspace("launch", { opinion }) });
  save(sent, record, 3);
  const { user } = renderAt(url);
  expect(await screen.findByText("Citation [1] has changed since you cited it (for example, it moved to a clearer copy of the page).")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cite [1] as it is now" }));
  await waitFor(() => expect(sent.at(-1)?.body).toMatchObject({ body: "See [[cite:1]].", citation_refs: [{ ...REF_1, fact_version: 2 }] }));
  await waitFor(() => expect(screen.queryByText(/has changed since you cited it/)).not.toBeInTheDocument());
});

test("the footer says how many questions have an opinion", async () => {
  serve("launch", { progress: progress({ answered: 2, total: 4 }) });
  renderAt(url);
  expect(await screen.findByText("2 of 4 questions answered")).toBeInTheDocument();
});

test("the question shows which paragraph of the instructions it came from", async () => {
  serve("launch");
  renderAt(url);
  expect(await screen.findByText("from paragraph 4.1 of your instructions")).toBeInTheDocument();
});

test("checks that found nothing say so, one line each, beside the one that found something", async () => {
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ lint: [lintIssue()] }) }) });
  renderAt(url);
  const checks = await screen.findByRole("region", { name: M["write.opinion.checks"] });
  expect(within(checks).getByText(M["write.opinion.check_loaded_ok"]!)).toBeInTheDocument();
  expect(within(checks).getByText(M["write.opinion.check_expertise_ok"]!)).toBeInTheDocument();
  expect(within(checks).queryByText(M["write.opinion.check_uncited_ok"]!)).not.toBeInTheDocument();
});
