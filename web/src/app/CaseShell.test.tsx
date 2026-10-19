// The case frame against the canvas: per-step progress on the pills, and "Where you are" on the case home.
import { screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../foundation/lib/screens";
import { http, json, server, signedIn } from "../test/api";
import { CASE_A, launchPack, PERSONAL_ORG, PERSON_ID, type Schemas } from "../test/fixtures";
import { renderAt } from "../test/render";

const theCase = {
  id: CASE_A, organisation_id: PERSONAL_ORG, named_expert_id: PERSON_ID, jurisdiction: "gb-eaw", case_type: "clin-neg-breach",
  specialty: "general-practice", expert_role: "single", label: "Mrs A", party_role: "other_side", instructing_party_type: null,
  court_track: null, due_date: "2026-11-30", status: "open", on_hold: false, created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z", allowed_actions: ["case.view_content"],
} as Schemas["Case"];
const steps = launchPack.workflow_steps as Array<{ key: string; screens?: string[] }>;
const readStep = steps.find((s) => s.screens?.includes("reader"))!;

afterEach(() => resetScreens());

function serve(progress: { read: number; total: number }) {
  registerScreen("reader", ({ caseId }) => `/cases/${caseId}/read-here`);
  signedIn();
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json(theCase)),
    http.get(`/v1/cases/${CASE_A}/progress`, () => json({ ...progress, seconds_left: 2400 })),
  );
}

test("the step that holds the reader shows how much is read", async () => {
  serve({ read: 1104, total: 1231 });
  renderAt(`/cases/${CASE_A}`);
  const bar = await screen.findByRole("banner");
  expect(await within(bar).findByRole("link", { name: /90%/ })).toHaveAccessibleName(new RegExp(`${readStep.key}.*90%`, "i"));
  expect(within(bar).getAllByText(/%/)).toHaveLength(1);
});

test("the case home says where you are, with a way to the unread pages", async () => {
  serve({ read: 1104, total: 1231 });
  renderAt(`/cases/${CASE_A}`);
  expect(await screen.findByRole("heading", { level: 2, name: "Where you are" })).toBeInTheDocument();
  expect(screen.getByText("1,104 of 1,231 pages read.")).toBeInTheDocument();
  expect(screen.getByText("127 to go.")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "90");
  expect(screen.getByText(/127 pages still to read/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Show me" })).toHaveAttribute("href", `/cases/${CASE_A}/read-here`);
});

test("when everything is read there is no banner", async () => {
  serve({ read: 10, total: 10 });
  renderAt(`/cases/${CASE_A}`);
  await screen.findByRole("heading", { level: 2, name: "Where you are" });
  expect(screen.queryByRole("link", { name: "Show me" })).toBeNull();
});

test("a case with no pages yet shows no progress", async () => {
  serve({ read: 0, total: 0 });
  renderAt(`/cases/${CASE_A}`);
  await screen.findByRole("banner");
  expect(screen.queryByRole("heading", { name: "Where you are" })).toBeNull();
  expect(screen.queryByText(/%/)).toBeNull();
});

test("the close screen has the case's one bar (logo, case, step links, avatar) and no strip of its own", async () => {
  signedIn();
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json({ ...theCase, status: "settled", allowed_actions: ["case.view_metadata"] })),
    http.get(`/v1/cases/${CASE_A}/close`, () =>
      json({ case_id: CASE_A, status: "settled", settled_at: "2027-07-14T10:00:00Z", retention_policy: "keep_default", delete_after: "2033-07-14", retention_years: 6, ever_served: true, holds: [], held: false, deletion_started: false, deleted_at: null, job_id: null }),
    ),
  );
  renderAt(`/cases/${CASE_A}/close`);
  const bar = await screen.findByRole("banner");
  // The mock-up (S26) puts the case name in the bar and "The case has settled" in the page.
  expect(within(bar).getByText(theCase.label)).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1, name: "The case has settled" })).toBeInTheDocument();
  expect(within(bar).getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  expect(screen.getAllByRole("navigation", { name: "Steps for this case" })).toHaveLength(1);
  expect(within(bar).getByRole("navigation", { name: "Steps for this case" })).toBeInTheDocument();
});
