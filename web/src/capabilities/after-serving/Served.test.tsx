// S19 After serving: what was served (locked, read-only), the dates, and the ways on to S20, S21, S21b and S29.
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { AFTER_MESSAGES as M } from "./words";
import { deadline, question, questionsName, questionsOne, served, serve, type PackRun } from "./testing";

const url = `/cases/${CASE_A}/served`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("says the report has been served and passes axe", async () => {
    serve(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: M["after_serving.served.title"] })).toBeInTheDocument();
    expect(screen.getByText("Sent 28 November 2026. This version is locked. Here's what comes next.")).toBeInTheDocument();
    expect(screen.getByText(M["after_serving.served.locked"]!)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("the way on to each of the four screens, with the answer date for questions", async () => {
  serve("launch", { deadlines: [deadline({ kind: "written_questions", due_on: "2026-12-26" })] });
  renderAt(url);
  expect(await screen.findByText("Answers are due by 26 December 2026.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["after_serving.served.questions_action"]! })).toHaveAttribute("href", `/cases/${CASE_A}/written-questions`);
  expect(screen.getByRole("link", { name: M["after_serving.served.new_action"]! })).toHaveAttribute("href", `/cases/${CASE_A}/new-material`);
  expect(screen.getByRole("link", { name: M["after_serving.served.changes_action"]! })).toHaveAttribute("href", `/cases/${CASE_A}/correct-report`);
  expect(screen.getByRole("link", { name: M["after_serving.served.orders_action"]! })).toHaveAttribute("href", `/cases/${CASE_A}/court-orders`);
});

test("the served report can be read but has no way to change it", async () => {
  serve("launch");
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.served.view"]! }));
  expect(await screen.findByText("Mrs A should have been referred in early March 2024.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("a report that hasn't been served says so", async () => {
  serve("launch", { served: served({ version: null, paragraphs: [] }) });
  renderAt(url);
  expect(await screen.findByText(M["after_serving.served.not_served"]!)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: M["after_serving.served.questions_action"]! })).toBeNull();
});

test("a failed load offers to try again", async () => {
  serve("launch");
  server.use(http.get("/v1/cases/:caseId/report/served", () => apiError(500, "server_error")));
  renderAt(url);
  expect(await screen.findByRole("button", { name: /try again/i })).toBeInTheDocument();
});

describe.each<PackRun>(["launch", "fixture"])("under the %s packs, the questions tile", (run) => {
  test("uses the pack's singular for one and its plural for more", async () => {
    serve(run, { questions: [question()] });
    renderAt(url);
    expect(await screen.findByRole("heading", { level: 3, name: `1 ${questionsOne(run)}` })).toBeInTheDocument();
  });
  test("uses the pack's plural for two", async () => {
    serve(run, { questions: [question(), question({ id: "q2", number: 2 })] });
    renderAt(url);
    expect(await screen.findByRole("heading", { level: 3, name: `2 ${questionsName(run)}` })).toBeInTheDocument();
  });
});
