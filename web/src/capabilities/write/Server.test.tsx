// Rule 4: the server decides which records count. The write screens render its answerable questions,
// its live points and its lint checks, and never filter or work them out in the browser.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { lintIssue, opinionOut, pointFact, pointOut, pointsBody, Q1, serve, sides, workspace } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

test("the question tabs ask the server for the answerable questions and show them as sent", async () => {
  serve("launch");
  let asked: string | null = null;
  server.use(
    http.get("/v1/cases/:caseId/questions", ({ request }) => {
      asked = new URL(request.url).searchParams.get("answerable");
      return json({ questions: [{ id: Q1, number: 5, status: "suggested", kind: "other", text_verbatim: "Sent first.", confirmed_text: null, para_ref: null, short_title: null }], total: 1 } as never);
    }),
  );
  renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  const tabs = await screen.findAllByRole("link", { name: /Question \d/ });
  expect(asked).toBe("true");
  expect(tabs.map((t) => t.textContent)).toEqual([expect.stringContaining("Question 5")]);
});

test("the points screen shows the server's live points, not its own filter of the list", async () => {
  const dismissed = pointOut({ id: "b2222222-2222-4222-8222-222222222222", status: "suggested", fact: pointFact({ value: "Hidden by the server" }) });
  const live = pointOut();
  serve("launch", { points: pointsBody("launch", { points: [live, dismissed], live: [live] } as never) });
  renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  const column = await screen.findByRole("region", { name: sides("launch")[0]!.label });
  expect(within(column).getByText("Bleeding for 3 weeks")).toBeInTheDocument();
  expect(screen.queryByText("Hidden by the server")).not.toBeInTheDocument();
});

test("a check the server says has not passed is not shown as passed, whatever warnings the browser holds", async () => {
  const checks = [
    { type: "uncited_fact", passed: false },
    { type: "loaded_language", passed: true },
    { type: "outside_expertise", passed: true },
  ];
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "x", lint: [], checks }), checks } as never) });
  renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
  const panel = await screen.findByRole("region", { name: M["write.opinion.checks"] });
  expect(within(panel).getByText(M["write.opinion.check_loaded_ok"]!)).toBeInTheDocument();
  expect(within(panel).queryByText(M["write.opinion.check_uncited_ok"]!)).not.toBeInTheDocument();
});

test("the passed checks are the server's, and a dismissal asks the server for the new state", async () => {
  const issue = lintIssue();
  const checks = (uncited: boolean) => [
    { type: "uncited_fact", passed: uncited },
    { type: "loaded_language", passed: true },
    { type: "outside_expertise", passed: true },
  ];
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "x", lint: [issue], checks: checks(false) }), checks: checks(false) } as never) });
  server.use(
    http.patch("/v1/cases/:caseId/lint-issues/:id", () => json({ ...issue, status: "dismissed" })),
    http.get("/v1/cases/:caseId/questions/:q/opinion/lint", () => json({ issues: [], open_issues: [], checks: checks(true) } as never)),
  );
  const { user } = renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
  const panel = await screen.findByRole("region", { name: M["write.opinion.checks"] });
  expect(within(panel).queryByText(M["write.opinion.check_uncited_ok"]!)).not.toBeInTheDocument();
  expect(within(panel).getByText(M["write.opinion.check_loaded_ok"]!)).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: M["write.opinion.issue_dismiss"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["write.opinion.dismiss_title"] });
  await user.type(within(dialog).getByLabelText(M["write.opinion.dismiss_reason"]!), "It is my own note");
  await user.click(within(dialog).getByRole("button", { name: M["write.opinion.dismiss_confirm"]! }));
  await waitFor(() => expect(within(panel).getByText(M["write.opinion.check_uncited_ok"]!)).toBeInTheDocument());
});
