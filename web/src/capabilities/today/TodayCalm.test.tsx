// S03a Today (calm): only what needs you, the week ahead and a side panel. The case list lives on Cases.
import { screen, within } from "@testing-library/react";
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

test("this week shows seven days and a case due in two days", async () => {
  signedIn({ today: data });
  renderAt("/today");
  const week = await screen.findByRole("region", { name: "This week" });
  const days = within(week).getAllByRole("listitem");
  expect(days).toHaveLength(7);
  expect(week).toHaveTextContent("Mrs A");
  expect(week).not.toHaveTextContent("Ms C");
  expect(within(week).getAllByText("Nothing due").length).toBeGreaterThanOrEqual(5);
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
