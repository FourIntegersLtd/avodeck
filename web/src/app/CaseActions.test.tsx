// The case header's action slot: capabilities register actions, shown when their predicate says so.
import { screen } from "@testing-library/react";
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

test("a registered action shows in the case header as a link when its predicate allows", async () => {
  registerCaseAction({
    key: "change_expert",
    order: 1,
    labelKey: "app.nav.profile",
    to: ({ caseId }) => `/cases/${caseId}/change-expert`,
    visible: (c) => c.isNamedExpert && !c.served && c.orgRole === "owner",
  });
  open();
  const link = await screen.findByRole("link", { name: "Your profile" });
  expect(link).toHaveAttribute("href", `/cases/${CASE_A}/change-expert`);
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
