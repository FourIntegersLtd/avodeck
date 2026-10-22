// S03 with the data the board shows: the greeting uses the short form the server sends ("Dr Morgan"),
// and each "needs you" row's icon comes from what its slice registered for the row's kind.
import { screen, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerMessages } from "../../foundation/i18n";
import { registerNeedsYouIcon } from "../../foundation/lib/needsYouIcons";
import { registerScreen } from "../../foundation/lib/screens";
import { signedIn } from "../../test/api";
import { CASE_A, caseCard, me, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";

beforeAll(() => {
  registerMessages({ "data.read.label": "{{count}} pages still to read", "data.read.action": "Carry on reading", "data.sign.label": "joint statement to sign", "data.sign.action": "Prepare" });
  registerScreen("data_read", ({ caseId }) => `/cases/${caseId}/read`);
  registerNeedsYouIcon("data_read", { glyph: "book", tone: "mint" });
  registerNeedsYouIcon("data_sign", { glyph: "checklist", tone: "amber" });
});

const row = (kind: string, rank: number, over: object = {}) => ({
  kind, case_id: CASE_A, rank, label_key: `data.${kind === "data_read" ? "read" : "sign"}.label`, action_key: `data.${kind === "data_read" ? "read" : "sign"}.action`,
  screen: "data_read", count: 127, minutes: null, due_at: null, params: {}, ...over,
});

test("the greeting uses the short name from the server", async () => {
  const person = me({ greeting_name: "Dr Morgan" });
  signedIn({ me: person });
  renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening), Dr Morgan$/ })).toBeInTheDocument();
});

test("without a short name the greeting falls back to the full name", async () => {
  signedIn({ me: me({ greeting_name: null }) });
  renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: /Dr Jane Morgan$/ })).toBeInTheDocument();
});

test("a row's icon is the glyph and tone its kind registered, not just whether it has a deadline", async () => {
  signedIn({ today: today({ cases: [caseCard({ case_id: CASE_A, label: "Mrs A" })], needs_you: [row("data_read", 1, { due_at: "2026-11-04T09:00:00Z" }), row("data_sign", 2)] }) });
  renderAt("/today");
  const items = within(await screen.findByRole("region", { name: "2 things need you" })).getAllByRole("listitem");
  expect(items[0]!.querySelector(".av-icon-tile--mint svg[data-glyph='book']")).not.toBeNull();
  expect(items[1]!.querySelector(".av-icon-tile--amber svg[data-glyph='checklist']")).not.toBeNull();
});

test("a kind nobody registered keeps the clock for a deadline and the book without one", async () => {
  signedIn({ today: today({ cases: [caseCard({ case_id: CASE_A, label: "Mrs A" })], needs_you: [row("data_other", 1, { due_at: "2026-11-04T09:00:00Z" }), row("data_unknown", 2)] }) });
  renderAt("/today");
  const items = within(await screen.findByRole("region", { name: "2 things need you" })).getAllByRole("listitem");
  expect(items[0]!.querySelector(".av-icon-tile--amber svg[data-glyph='clock']")).not.toBeNull();
  expect(items[1]!.querySelector(".av-icon-tile--mint svg[data-glyph='book']")).not.toBeNull();
});
