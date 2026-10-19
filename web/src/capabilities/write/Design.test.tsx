// The S14 and S15 board details that need server data: tab titles, the question's kind label from the
// pack, "Dictated {date}" on the range note, and the flagged sentence marked in the opinion text.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { humanise } from "../../foundation/i18n";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { header, lintIssue, opinionOut, pointsBody, Q1, serve, workspace, type PackRun } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the question tabs read 'Q1 · title' with the server's short title", async () => {
    serve(run);
    renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
    const tab = await screen.findByRole("link", { name: /Question 1/ });
    expect(tab).toHaveTextContent("Q1 · February bloods");
    expect(await screen.findByRole("link", { name: /Question 2/ })).toHaveTextContent("Q2 · 15 May visit");
  });

  test("screen 15 names the question's kind with the pack's label and the paragraph it came from", async () => {
    const head = header(run, { para_ref: "4.2" });
    serve(run, { workspace: workspace(run, { question: head }) });
    renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
    const label = humanise(head.kind);
    expect(await screen.findByText(new RegExp(`^Q1 · ${label}$`))).toBeInTheDocument();
    expect(screen.getByText("from paragraph 4.2 of your instructions")).toBeInTheDocument();
  });

  test("screen 14 shows the kind label beside the question", async () => {
    const head = header(run);
    serve(run, { points: pointsBody(run, { question: head }) });
    renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
    expect(await screen.findByText(new RegExp(`^Q1 · ${humanise(head.kind)}$`))).toBeInTheDocument();
  });
});

test("the range note says when it was first dictated, and says nothing before it exists", async () => {
  const none = serve("launch");
  const { unmount } = renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  await screen.findByRole("heading", { level: 2, name: M["write.points.title"] });
  expect(screen.queryByText(/^Dictated /)).not.toBeInTheDocument();
  unmount();
  none.sent.length = 0;
  serve("launch", { points: pointsBody("launch", { range_note: { prompt: "What others might do", text: "Some would wait", version: 1, dictated_on: "2024-10-14" } }) });
  renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  expect(await screen.findByText("Dictated 14 Oct")).toBeInTheDocument();
});

test("a flagged sentence is marked in a read-only copy of the text, with citations as page numbers", async () => {
  const body = "It began well. Review was due in 3 weeks. Then it ended.";
  const issue = lintIssue({ sentence_start: 15, sentence_end: 41, words: "3" });
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body, lint: [issue] }) }) });
  renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
  const copy = await screen.findByRole("region", { name: M["write.opinion.flagged_copy"] });
  const marked = within(copy).getByText("Review was due in 3 weeks.");
  expect(marked.tagName).toBe("MARK");
  expect(within(copy).queryByText("It began well.", { selector: "mark" })).not.toBeInTheDocument();
});

test("with no flagged sentence there is no marked copy", async () => {
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "All fine.", lint: [] }) }) });
  renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
  await screen.findByRole("heading", { level: 2, name: M["write.opinion.title"] });
  expect(screen.queryByRole("region", { name: M["write.opinion.flagged_copy"] })).not.toBeInTheDocument();
});
