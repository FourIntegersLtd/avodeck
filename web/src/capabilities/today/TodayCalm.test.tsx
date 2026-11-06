// S03a Today (calm): only what needs you, the week ahead and a side panel. The case list lives on Cases.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, expect, test } from "vitest";
import { registerMessages } from "../../foundation/i18n";
import { registerScreen } from "../../foundation/lib/screens";
import { signedIn } from "../../test/api";
import { CASE_A, CASE_B, caseCard, PERSONAL_ORG, TEAM_ORG, today } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

beforeAll(() => {
  registerMessages({ "wk.read.label": "{{case}} · {{count}} pages still to read", "wk.read.action": "Carry on reading" });
  registerScreen("wk_reader", ({ caseId }) => `/cases/${caseId}/wk`);
  registerScreen("new_case", () => "/cases/new");
});

const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);

const data = today({
  cases: [
    caseCard({ case_id: CASE_A, label: "Mrs A", due_date: inDays(2) }),
    caseCard({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team", due_date: inDays(40) }),
  ],
  organisations: [
    { id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 1 },
    { id: TEAM_ORG, name: "Northgate", kind: "team", open_count: 1 },
  ],
  needs_you: [{ kind: "pages", case_id: CASE_B, rank: 1, label_key: "wk.read.label", action_key: "wk.read.action", screen: "wk_reader", count: 12, minutes: null, due_at: null, params: {} }],
});

test("Today has no case list: the cases live on Cases", async () => {
  signedIn({ today: data });
  const { container } = renderAt("/today");
  await screen.findByRole("region", { name: "1 thing needs you" });
  expect(container.querySelector(".av-cases, .av-case-card")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Your cases" })).toBeNull();
});

test("this week is one short line, not seven empty boxes", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const week = await screen.findByRole("region", { name: "This week" });
  expect(week).toHaveTextContent("1 due this week");
  expect(within(week).queryByText("Nothing due")).toBeNull();
  expect(within(week).queryAllByRole("listitem")).toHaveLength(0);
  expect(week).not.toHaveTextContent("Ms C");
});

test("opening this week shows what is due in a box, with a link to the case", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const week = await screen.findByRole("region", { name: "This week" });
  await userEvent.click(within(week).getByRole("button", { name: "See what is due this week" }));
  const box = await screen.findByRole("dialog", { name: "This week" });
  expect(box).toHaveTextContent("Dates in your time zone");
  expect(within(box).getByRole("link", { name: /Mrs A/ })).toHaveAttribute("href", `/cases/${CASE_A}/home`);
  expect(box).not.toHaveTextContent("Ms C");
  await userEvent.click(within(box).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a week with nothing due says so once", async () => {
  signedIn({ today: today({ ...data, cases: [caseCard({ case_id: CASE_A, label: "Mrs A", due_date: inDays(40) })], needs_you: [] }) });
  renderAt("/today");
  const week = await screen.findByRole("region", { name: "This week" });
  expect(week).toHaveTextContent("Nothing due this week");
  await userEvent.click(within(week).getByRole("button", { name: "See what is due this week" }));
  expect(await screen.findByRole("dialog", { name: "This week" })).toHaveTextContent("Nothing due this week");
});

test("pick up where you left off opens the case that needs you most", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const panel = await screen.findByRole("region", { name: "Pick up where you left off" });
  expect(panel).toHaveTextContent("Ms C");
  expect(within(panel).getByRole("link", { name: "Open this case" })).toHaveAttribute("href", `/cases/${CASE_B}/home`);
});

test("at a glance counts the open cases and links to all of them", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const glance = await screen.findByRole("region", { name: "At a glance" });
  expect(glance).toHaveTextContent("Open cases");
  expect(glance).toHaveTextContent("2");
  expect(within(glance).getByRole("link", { name: "See all your cases" })).toHaveAttribute("href", "/cases");
});

test("Start a new case is quiet while something needs you, and passes axe", async () => {
  signedIn({ today: data });
  const { container } = renderAt("/today");
  expect(await screen.findByRole("link", { name: "Start a new case" })).toHaveClass("av-button--secondary");
  expect(await axeViolations(container)).toEqual([]);
});

test("Today is the current place in the header", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(await within(header).findByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
  expect(within(header).getByRole("link", { name: "Cases" })).not.toHaveAttribute("aria-current");
});
