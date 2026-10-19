// S17 Final check: what failed with a "Fix it" way in, what passed, the tone warnings as places (never edits),
// running the check, and the colleague spot-check (link once, the code by email).
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { humanise } from "../../foundation/i18n";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { checkResult, failedCheck, finalCheck, PARA, sectionLabel, serve, spotOut, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/final-check`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows what failed first, with a way to fix it, and passes axe", async () => {
    serve(run, { check: failedCheck() });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 1, name: "Final check: 1 thing to fix" })).toBeInTheDocument();
    const failed = screen.getByRole("listitem", { name: "Question 4 hasn't been answered yet." });
    expect(within(failed).getByRole("link", { name: M["report.check.fix"]! })).toHaveAttribute("href", `/cases/${CASE_A}/opinion`);
    expect(screen.getByText(M["report.check.not_run"]!)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("a clean run says all clear, counts what passed and offers signing", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: M["report.check.title_ok"]! })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "2 of 2 checks passed" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["report.check.sign"]! })).toHaveAttribute("href", `/cases/${CASE_A}/sign`);
});

test("while something fails, signing is not offered", async () => {
  serve("launch", { check: failedCheck() });
  renderAt(url);
  expect(await screen.findByText("Fix 1 thing first")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: M["report.check.sign"]! })).toBeDisabled();
  expect(screen.queryByRole("link", { name: M["report.check.sign"]! })).toBeNull();
});

test("a check name falls back to the key in plain words when the API sends none", async () => {
  serve("launch", { check: finalCheck({ passed: false, results: [checkResult({ key: "some_new_check", outcome: "failed", text: null, count: 2 })] }) });
  renderAt(url);
  expect(await screen.findByText(humanise("some_new_check"))).toBeInTheDocument();
});

test("before the first run it says so and runs the check, then shows the result", async () => {
  serve("launch", { check: null });
  let ran = false;
  server.use(
    http.post("/v1/cases/:caseId/report/final-check", () => ((ran = true), json({ job_id: "j" }, 202))),
    http.get("/v1/cases/:caseId/report/final-check/latest", () => (ran ? json(finalCheck()) : apiError(404, "not_found"))),
  );
  const { user } = renderAt(url);
  expect(await screen.findByText(M["report.check.none"]!)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["report.check.run"]! }));
  expect(await screen.findByText(M["report.check.running"]!)).toBeInTheDocument();
  expect(await screen.findByRole("heading", { level: 1, name: M["report.check.title_ok"]! })).toBeInTheDocument();
});

test("running the check before a report is built points to the build", async () => {
  serve("launch", { check: null });
  server.use(http.post("/v1/cases/:caseId/report/final-check", () => apiError(409, "not_built")));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.check.run"]! }));
  expect(await screen.findByText(M["report.check.no_build"]!)).toBeInTheDocument();
});

test("an out-of-date check asks to be run again, and changed declaration wording is flagged", async () => {
  serve("launch", { check: finalCheck({ current: false, declaration_changed: true }) });
  renderAt(url);
  expect(await screen.findByText(M["report.check.out_of_date"]!)).toBeInTheDocument();
  expect(screen.getByText(M["report.check.declaration_changed"]!)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: M["report.check.sign"]! })).toBeNull();
});

test("tone and expertise warnings are places in the report, with the flagged words, and nothing to accept", async () => {
  const warning = { paragraph_id: PARA, para_no: 14, section: "opinion", question_id: null, type: "loaded_language", words: "clearly negligent" };
  serve("launch", { check: finalCheck({ warnings: [warning] }) });
  renderAt(url);
  const list = await screen.findByRole("region", { name: M["report.check.warnings"]! });
  expect(within(list).getByText(M["report.check.warning.loaded_language"]!)).toBeInTheDocument();
  expect(within(list).getByText(`Section ${sectionLabel("launch", "opinion")}, paragraph 14`)).toBeInTheDocument();
  expect(within(list).getByText("clearly negligent")).toBeInTheDocument();
});

test("a colleague spot-check sends the email, shows the link once and says the code goes by email", async () => {
  const sent: unknown[] = [];
  serve("launch", { check: finalCheck() });
  server.use(
    http.post("/v1/cases/:caseId/spot-checks", async ({ request }) => {
      sent.push(await request.json());
      return json({ id: "d1", token: "tok-123", fact_count: 10, expires_at: "2026-10-25T10:00:00Z" }, 201);
    }),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.check.spot"]! }));
  await user.type(await screen.findByLabelText(M["report.check.spot_email"]!), "colleague@example.test");
  await user.click(screen.getByRole("button", { name: M["report.check.spot_send"]! }));
  expect(await screen.findByText(M["report.check.spot_sent"]!)).toBeInTheDocument();
  expect(screen.getByText("Link: /spot-check/tok-123")).toBeInTheDocument();
  expect(sent).toEqual([{ reviewer_email: "colleague@example.test" }]);
  expect(screen.queryByText(/tok-123/, { selector: "a" })).toBeNull();
});

test("past spot-checks show who answered, what they agreed with and any facts they disagreed with", async () => {
  const answered = spotOut({
    responded_at: "2026-10-19T10:00:00Z",
    responses: [
      { fact_id: "f1", agrees: true, reason: null },
      { fact_id: "f2", agrees: false, reason: "The page says 14 not 41" },
    ],
  });
  serve("launch", { check: finalCheck(), spots: [answered, spotOut({ id: "d2", reviewer_email: "waiting@example.test" }), spotOut({ id: "d3", reviewer_email: "locked@example.test", locked: true })] });
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.check.spot"]! }));
  expect(await screen.findByText("colleague@example.test: 1 of 2 facts agreed")).toBeInTheDocument();
  expect(screen.getByText("The page says 14 not 41", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Waiting for waiting@example.test")).toBeInTheDocument();
  expect(screen.getByText("locked@example.test: locked after wrong codes")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/token/i)).toBeNull());
});

test("passed checks are status rows with their counts, and the rest sit behind See all N checks", async () => {
  const results = Array.from({ length: 9 }, (_, i) => checkResult({ key: `check_${i}`, text: `Passed check ${i}`, count: i === 0 ? 214 : 0 }));
  serve("launch", { check: finalCheck({ results }) });
  const { user } = renderAt(url);
  const first = await screen.findByRole("listitem", { name: "Passed check 0" });
  expect(within(first).getByText("214")).toBeInTheDocument();
  expect(screen.queryByText("Passed check 8")).toBeNull();
  await user.click(screen.getByRole("button", { name: "See all 9 checks" }));
  expect(screen.getByText("Passed check 8")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /See all/ })).toBeNull();
});
