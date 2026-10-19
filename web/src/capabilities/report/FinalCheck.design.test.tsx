// S17 as drawn on the board: a score ring beside the title, one amber outlined card for each thing to fix, the
// passed checks as one list with their counts, and the colleague spot-check as a single link that opens its form.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { checkResult, failedCheck, finalCheck, serve, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/final-check`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the title has a ring that counts what passed, and the screen is axe-clean", async () => {
    serve(run, { check: failedCheck() });
    const { container } = renderAt(url);
    expect(await screen.findByRole("img", { name: "1 of 3 checks passed" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: /^Final check/ })).toHaveTextContent("Final check: 1 thing to fix");
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("each thing to fix is an amber outlined card with an icon and a solid Fix it button", async () => {
  serve("launch", { check: failedCheck() });
  renderAt(url);
  const card = await screen.findByRole("listitem", { name: "Question 4 hasn't been answered yet." });
  expect(card).toHaveClass("rp-fix");
  expect(card.querySelector(".av-icon-tile--amber")).not.toBeNull();
  const fix = within(card).getByRole("link", { name: M["report.check.fix"]! });
  expect(fix).toHaveClass("av-button");
  expect(fix).not.toHaveClass("av-button--secondary");
});

test("a check that could not run shows in the same list with its own words", async () => {
  serve("launch", { check: failedCheck() });
  renderAt(url);
  const row = await screen.findByRole("listitem", { name: "The age agrees with the date of birth" });
  expect(within(row).getByText(M["report.check.not_run"]!)).toBeInTheDocument();
  expect(row.querySelector(".av-icon-tile--amber")).not.toBeNull();
});

test("the passed checks are one list with no heading card, and a count at the right", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  const list = await screen.findByRole("list", { name: M["report.check.passed"]! });
  const rows = within(list).getAllByRole("listitem");
  expect(rows).toHaveLength(2);
  expect(within(rows[1]!).getByText("214")).toHaveClass("av-mono");
  expect(screen.queryByRole("heading", { name: M["report.check.passed"]! })).toBeNull();
});

test("See all N checks sits on the last row as a link-styled button", async () => {
  const results = Array.from({ length: 9 }, (_, i) => checkResult({ key: `c${i}`, text: `Passed check ${i}` }));
  serve("launch", { check: finalCheck({ results }) });
  renderAt(url);
  expect(await screen.findByRole("button", { name: "See all 9 checks" })).toHaveClass("av-text-link");
});

test("the spot-check is one link; its form opens only when asked for", async () => {
  serve("launch", { check: finalCheck() });
  const { user } = renderAt(url);
  const open = await screen.findByRole("button", { name: M["report.check.spot"]! });
  expect(open).toHaveClass("av-text-link");
  expect(screen.queryByLabelText(M["report.check.spot_email"]!)).toBeNull();
  await user.click(open);
  expect(await screen.findByLabelText(M["report.check.spot_email"]!)).toBeInTheDocument();
});

test("while something fails the bar says so beside a grey Sign and send; Check again stays reachable", async () => {
  serve("launch", { check: failedCheck() });
  renderAt(url);
  expect(await screen.findByText("Fix 1 thing first")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: M["report.check.sign"]! })).toBeDisabled();
  expect(screen.getByRole("button", { name: M["report.check.run_again"]! })).toBeInTheDocument();
});

test("a clean run offers Sign and send as the one main action, with an arrow", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  const sign = await screen.findByRole("link", { name: M["report.check.sign"]! });
  expect(sign.querySelector("svg")).not.toBeNull();
});
