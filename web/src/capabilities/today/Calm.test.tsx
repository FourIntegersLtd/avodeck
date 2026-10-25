// S03 in the calm design: one green primary button, quiet everything else, hairline rows, soft cards.
import { screen, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerMessages } from "../../foundation/i18n";
import { registerScreen } from "../../foundation/lib/screens";
import { signedIn } from "../../test/api";
import { CASE_A, CASE_B, caseCard, PERSONAL_ORG, TEAM_ORG, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";

beforeAll(() => {
  registerMessages({ "calm.unread.label": "{{case}} · {{count}} pages still to read", "calm.unread.action": "Carry on reading", "calm.check.action": "Fix them" });
  registerScreen("calm_reader", ({ caseId }) => `/cases/${caseId}/calm`);
  registerScreen("new_case", () => "/cases/new");
});

const full = today({
  cases: [caseCard({ case_id: CASE_A, label: "Mrs A" }), caseCard({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team", open_count: 1 })],
  organisations: [
    { id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 1 },
    { id: TEAM_ORG, name: "Northgate", kind: "team", open_count: 1 },
  ],
  needs_you: [
    { kind: "pages", case_id: CASE_A, rank: 1, label_key: "calm.unread.label", action_key: "calm.unread.action", screen: "calm_reader", count: 127, minutes: null, due_at: null, params: {} },
    { kind: "pages", case_id: CASE_B, rank: 2, label_key: "calm.unread.label", action_key: "calm.check.action", screen: "calm_reader", count: 3, minutes: null, due_at: null, params: {} },
  ],
});

const isPrimary = (el: Element) => el.classList.contains("av-button") && ![...el.classList].some((c) => /^av-button--(secondary|outline|quiet|link|danger)$/.test(c));

test("at most one green primary button on the screen", async () => {
  signedIn({ today: full });
  const { container } = renderAt("/today");
  await screen.findByRole("region", { name: "2 things need you" });
  expect([...container.querySelectorAll(".av-button")].filter(isPrimary)).toHaveLength(1);
  expect(screen.getByRole("link", { name: "New case" })).toHaveClass("av-button--secondary");
});

test("with nothing waiting, New case is the one primary button", async () => {
  signedIn({ today: today({ cases: [], needs_you: [] }) });
  const { container } = renderAt("/today");
  await screen.findByText("You have no open cases.");
  expect([...container.querySelectorAll(".av-button")].filter(isPrimary)).toHaveLength(1);
  expect(screen.getByRole("link", { name: "New case" })).not.toHaveClass("av-button--secondary");
});

test("the organisation filter is the calm segmented control, not a row of buttons", async () => {
  signedIn({ today: full });
  const { container } = renderAt("/today");
  const filter = await screen.findByRole("group", { name: "Show cases for" });
  expect(filter).toHaveClass("av-segmented-control");
  expect(within(filter).getAllByRole("button")).toHaveLength(3);
  expect(container.querySelector(".av-filter")).toBeNull();
});

test("needs-you rows are hairline rows and case cards are soft cards with no inline styles", async () => {
  signedIn({ today: full });
  const { container } = renderAt("/today");
  const region = await screen.findByRole("region", { name: "2 things need you" });
  expect(region).not.toHaveClass("av-card");
  expect(region.querySelectorAll("li.av-needs__item.av-hairline-row")).toHaveLength(2);
  const card = (await screen.findAllByRole("link", { name: /Mrs A/ })).find((l) => l.classList.contains("av-case-card"))!;
  expect(card).toHaveClass("av-card");
  expect(container.querySelector("[style]")).toBeNull();
});
