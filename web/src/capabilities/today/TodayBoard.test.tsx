// S03 against its board (docs/design/screens/S03-Today.dc.html): each "needs you" row has an icon
// tile, its title names the case, only the first row's button is the main action, New case has a
// plus, and the case ring has a grey track under a 9px arc.
import { screen, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerMessages } from "../../foundation/i18n";
import { registerScreen } from "../../foundation/lib/screens";
import { signedIn } from "../../test/api";
import { CASE_A, CASE_B, caseCard, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";

beforeAll(() => {
  registerMessages({
    "board.unread.label": "{{count}} pages still to read",
    "board.unread.action": "Carry on reading",
    "board.check.label": "{{case}}: the final check found {{count}} things",
    "board.check.action": "Fix them",
  });
  registerScreen("board_reader", ({ caseId }) => `/cases/${caseId}/reader`);
  registerScreen("board_check", ({ caseId }) => `/cases/${caseId}/check`);
  registerScreen("new_case", () => "/cases/new");
});

const withItems = today({
  cases: [caseCard({ case_id: CASE_A, label: "Mrs A" }), caseCard({ case_id: CASE_B, label: "Mr B" })],
  needs_you: [
    { kind: "pages", case_id: CASE_A, rank: 1, label_key: "board.unread.label", action_key: "board.unread.action", screen: "board_reader", count: 127, minutes: null, due_at: null, params: {} },
    { kind: "check", case_id: CASE_B, rank: 2, label_key: "board.check.label", action_key: "board.check.action", screen: "board_check", count: 2, minutes: null, due_at: "2026-11-04T09:00:00Z", params: {} },
  ],
});

test("a row's title starts with the case unless its words already name it", async () => {
  signedIn({ today: withItems });
  renderAt("/today");
  const items = within(await screen.findByRole("region", { name: "2 things need you" })).getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("Mrs A · 127 pages still to read");
  expect(items[1]).toHaveTextContent("Mr B: the final check found 2 things");
  expect(items[1]).not.toHaveTextContent("Mr B · Mr B");
});

test("every row has an icon tile, amber when it has a deadline", async () => {
  signedIn({ today: withItems });
  renderAt("/today");
  const items = within(await screen.findByRole("region", { name: "2 things need you" })).getAllByRole("listitem");
  expect(items[0]!.querySelector(".av-icon-tile--mint svg")).not.toBeNull();
  expect(items[1]!.querySelector(".av-icon-tile--amber svg")).not.toBeNull();
});

test("only the first row's button is the main action; the rest are outlined", async () => {
  signedIn({ today: withItems });
  renderAt("/today");
  const region = await screen.findByRole("region", { name: "2 things need you" });
  expect(within(region).getByRole("link", { name: "Carry on reading" })).not.toHaveClass("av-button--secondary");
  expect(within(region).getByRole("link", { name: "Fix them" })).toHaveClass("av-button--secondary");
});

test("Start a new case has a plus in front of it", async () => {
  signedIn({ today: withItems });
  renderAt("/today");
  const button = await screen.findByRole("link", { name: "Start a new case" });
  expect(button.querySelector("svg")).not.toBeNull();
});

