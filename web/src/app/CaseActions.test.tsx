// The case header's action slot: capabilities register actions, shown when their predicate says so.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { registerCaseAction, resetSlots } from "../foundation/lib/slots";
import { http, json, server, signedIn } from "../test/api";
import { CASE_A, PERSONAL_ORG, PERSON_ID, type Schemas } from "../test/fixtures";
import { renderAt } from "../test/render";

afterEach(() => resetSlots());

const theCase = {
  id: CASE_A,
  organisation_id: PERSONAL_ORG,
  named_expert_id: PERSON_ID,
  jurisdiction: "gb-eaw",
  case_type: "clin-neg-breach",
  specialty: "general-practice",
  expert_role: "single",
  label: "Mrs A",
  party_role: "other_side",
  instructing_party_type: null,
  court_track: null,
  due_date: "2026-11-30",
  status: "active",
  on_hold: false,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  allowed_actions: ["case.view_content"],
} as Schemas["Case"];

function open(over: Partial<Schemas["Case"]> = {}) {
  signedIn();
  server.use(http.get(`/v1/cases/${CASE_A}`, () => json({ ...theCase, ...over })));
  return renderAt(`/cases/${CASE_A}`);
}

// Reason: actions are no longer links in the bar; they decide whether the avatar menu offers "Case settings".
test("a registered action puts one Case settings link in the avatar menu, not in the bar", async () => {
  registerCaseAction({
    key: "change_expert",
    order: 1,
    labelKey: "app.nav.profile",
    to: ({ caseId }) => `/cases/${caseId}/team`,
    visible: (c) => c.isNamedExpert && !c.served && c.orgRole === "owner",
  });
  open();
  await screen.findByRole("heading", { level: 1, name: "Mrs A" });
  expect(screen.queryByRole("link", { name: "Your profile" })).toBeNull();
  expect(document.querySelector(".av-case-bar__actions")).toBeNull();
  await userEvent.setup().click(screen.getByRole("button", { name: "Your account" }));
  expect(screen.getByRole("link", { name: "Case settings" })).toHaveAttribute("href", `/cases/${CASE_A}/team`);
});

test("the predicate sees whether the case is served", async () => {
  registerCaseAction({ key: "x", order: 1, labelKey: "app.case.back", to: () => "/x", visible: (c) => !c.served });
  open({ status: "served" });
  await screen.findByRole("heading", { level: 1, name: "Mrs A" });
  expect(document.querySelector('a[href="/x"]')).toBeNull();
});

test("with nothing registered there are no extra links in the header", async () => {
  open();
  await screen.findByRole("heading", { level: 1, name: "Mrs A" });
  expect(document.querySelector(".av-case-bar__actions")).toBeNull();
});
