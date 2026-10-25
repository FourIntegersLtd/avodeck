// S03 Today: greeting, the "needs you" items slices register, and case cards across organisations.
import { screen, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { registerMessages } from "../../foundation/i18n";
import { signedIn } from "../../test/api";
import { CASE_A, CASE_B, caseCard, PERSONAL_ORG, TEAM_ORG, today } from "../../test/fixtures";

import { axeViolations, renderAt } from "../../test/render";

beforeAll(() => {
  // What a slice does to put its items on Today: its words and the screen its button opens.
  registerMessages({
    "example.unread.label": "{{case}} · {{count}} pages still to read",
    "example.unread.detail": "About {{minutes}} minutes left.",
    "example.unread.action": "Carry on reading",
  });
  registerScreen("example_reader", ({ caseId }) => `/cases/${caseId}/example`);
});

const twoOrgs = today({
  cases: [
    caseCard({ case_id: CASE_A, label: "Mrs A", stage: "read", due_date: "2026-11-30" }),
    caseCard({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team", stage: "court", party_role: "instructing_side", due_date: "2026-10-21" }),
  ],
  organisations: [
    { id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 1 },
    { id: TEAM_ORG, name: "Northgate", kind: "team", open_count: 1 },
  ],
  closed_count: 23,
  needs_you: [
    { kind: "pages_to_read", case_id: CASE_A, rank: 20, label_key: "example.unread.label", action_key: "example.unread.action", screen: "example_reader", count: 127, minutes: 40, due_at: null, params: {} },
    { kind: "unknown_thing", case_id: CASE_B, rank: 10, label_key: "nobody.registered.this", action_key: "nobody.registered.action", screen: "nowhere", count: null, minutes: null, due_at: null, params: {} },
  ],
});

test("greets the expert by name and passes axe", async () => {
  signedIn({ today: twoOrgs });
  const { container } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening), Dr Jane Morgan$/ })).toBeInTheDocument();
  await screen.findByRole("region", { name: "2 things need you" });
  expect(await axeViolations(container)).toEqual([]);
});

test("shows the registered items, ranked, with their words and buttons", async () => {
  signedIn({ today: twoOrgs });
  renderAt("/today");
  const list = await screen.findByRole("region", { name: "2 things need you" });
  const items = within(list).getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("Ms C needs you");
  expect(items[1]).toHaveTextContent("Mrs A · 127 pages still to read");
  expect(within(items[1]!).getByRole("link", { name: "Carry on reading" })).toHaveAttribute("href", `/cases/${CASE_A}/example`);
  expect(within(items[0]!).getByRole("link", { name: "Open" })).toHaveAttribute("href", `/cases/${CASE_B}`);
});

test("nothing to do says so", async () => {
  signedIn({ today: today({ needs_you: [] }) });
  renderAt("/today");
  expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
});

test("New case appears once a slice registers the screen", async () => {
  signedIn();
  registerScreen("new_case", () => "/cases/new");
  renderAt("/today");
  expect(await screen.findByRole("link", { name: "Start a new case" })).toHaveAttribute("href", "/cases/new");
});
