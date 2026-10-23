// Inside a case: change the named expert (owners and admins, before serving) and the support grant card.
import { screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { CASE_A, PERSON_ID, TEAM_ORG } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { setFlag } from "./flags";
import { MEMBERS, OTHER, recorder, THIRD } from "./testing";

afterEach(() => setFlag("replacement_case", false));

function theCase(actions: string[], status = "active") {
  return {
    allowed_actions: actions, case_type: "clin-neg-breach", court_track: null, created_at: "2026-01-05T09:00:00Z", due_date: null, expert_role: "single",
    id: CASE_A, instructing_party_type: null, jurisdiction: "gb-eaw", label: "Mrs A", named_expert_id: PERSON_ID, on_hold: false, organisation_id: TEAM_ORG,
    party_role: null, specialty: "general-practice", status, updated_at: "2026-01-05T09:00:00Z",
  };
}

function world(actions: string[], status?: string) {
  signedIn();
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json(theCase(actions, status))),
    http.get(`/v1/organisations/${TEAM_ORG}/members`, () => json({ members: MEMBERS })),
    http.get(`/v1/cases/${CASE_A}/support-access`, () => json({ grants: [] })),
  );
}

test("an admin can move the case to another expert, and the request names the person", async () => {
  world(["case.view_metadata", "case.reassign_named_expert"]);
  const { sent, record } = recorder();
  server.use(http.post(`/v1/cases/${CASE_A}/named-expert`, async ({ request }) => (await record(request), json({ case_id: CASE_A, named_expert_id: THIRD }))));
  const { user } = renderAt(`/cases/${CASE_A}/team`);
  await user.click(await screen.findByRole("button", { name: "Change named expert" }));
  const choose = screen.getByLabelText("New named expert");
  expect(within(choose).queryByRole("option", { name: /Jane Morgan/ })).toBeNull(); // the current named expert
  await user.selectOptions(choose, THIRD);
  await user.click(screen.getByRole("button", { name: "Move the case" }));
  expect(await screen.findByText("The case now belongs to Lena Hart.")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ person_id: THIRD });
});

test("without the action there is no change button and no support card", async () => {
  world(["case.view_metadata"]);
  renderAt(`/cases/${CASE_A}/team`);
  await screen.findByRole("heading", { name: "Who can work on this case" });
  expect(screen.queryByRole("button", { name: "Change named expert" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Avodeck support" })).toBeNull();
});

test("after serving the change is refused in words, and a replacement is offered only when the flag is on", async () => {
  world(["case.view_metadata", "case.reassign_named_expert"], "served");
  const first = renderAt(`/cases/${CASE_A}/team`);
  expect(await screen.findByText("A report has been served on this case, so its expert can't change.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create a replacement case" })).toBeNull();
  first.unmount();
  setFlag("replacement_case", true);
  renderAt(`/cases/${CASE_A}/team`);
  expect(await screen.findByRole("button", { name: "Create a replacement case" })).toBeInTheDocument();
});

test("a server refusal after serving shows the same message", async () => {
  world(["case.view_metadata", "case.reassign_named_expert"]);
  server.use(http.post(`/v1/cases/${CASE_A}/named-expert`, () => apiError(409, "served_use_replacement")));
  const { user } = renderAt(`/cases/${CASE_A}/team`);
  await user.click(await screen.findByRole("button", { name: "Change named expert" }));
  await user.selectOptions(screen.getByLabelText("New named expert"), OTHER);
  await user.click(screen.getByRole("button", { name: "Move the case" }));
  expect(await screen.findByText("A report has been served on this case, so its expert can't change.")).toBeInTheDocument();
});

test("the named expert can let support open the case, sees the time left, and can stop it", async () => {
  signedIn();
  const { sent, record } = recorder();
  let grants: unknown[] = [];
  const grant = () => ({ id: OTHER, case_id: CASE_A, granted_by: PERSON_ID, granted_to: null, reason: "help", granted_at: new Date().toISOString(), expires_at: new Date(Date.now() + 23.5 * 3_600_000).toISOString(), revoked_at: null, active: true });
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json(theCase(["case.view_metadata", "support_access.grant"]))),
    http.get(`/v1/cases/${CASE_A}/support-access`, () => json({ grants })),
    http.post(`/v1/cases/${CASE_A}/support-access`, async ({ request }) => {
      await record(request);
      grants = [grant()];
      return json(grants[0], 201);
    }),
    http.delete(`/v1/cases/${CASE_A}/support-access/${OTHER}`, async ({ request }) => {
      await record(request);
      grants = [];
      return new Response(null, { status: 204 });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/team`);
  await user.selectOptions(await screen.findByLabelText("Why do you want help?"), "help");
  await user.click(screen.getByRole("button", { name: "Let support open this case" }));
  expect(await screen.findByText(/Time left: 23 hours 29 minutes|Time left: 23 hours 30 minutes/)).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ reason: "help" });
  await user.click(screen.getByRole("button", { name: "Stop support opening this case" }));
  expect(await screen.findByText("Support can no longer open this case.")).toBeInTheDocument();
  expect(sent[1]!.method).toBe("DELETE");
});

test("passes the automated accessibility checks", async () => {
  world(["case.view_metadata", "case.reassign_named_expert", "support_access.grant"]);
  const { container } = renderAt(`/cases/${CASE_A}/team`);
  await screen.findByRole("button", { name: "Change named expert" });
  expect(await axeViolations(container)).toEqual([]);
});
