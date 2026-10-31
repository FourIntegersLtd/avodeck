// The support console's other look-ups (support plan phase 2): a person by their sign-in email, a practice or
// team by its number, and how the service is running. Plain answers only: no names, no emails shown back.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_A, PERSONAL_ORG, me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

vi.setConfig({ testTimeout: 30_000 });

const PERSON = "33333333-3333-4333-8333-333333333333";
const INVITE = "44444444-4444-4444-8444-444444444444";
const LOCKED_UNTIL = new Date(Date.now() + 10 * 60_000).toISOString();

const person = (over: Record<string, unknown> = {}) => ({
  person_id: PERSON, created_at: "2026-01-05T09:00:00Z", closed_at: null, mfa_enrolled: true, mfa_locked_until: LOCKED_UNTIL,
  recovery_codes_left: 2, last_mfa_at: "2026-10-24T08:00:00Z", pending_email_change: true, custodian_set: false,
  blocking_case_ids_for_closure: [CASE_A], ...over,
});

const organisation = {
  organisation_id: PERSONAL_ORG, kind: "team", created_at: "2026-01-05T09:00:00Z", closed_at: null, owner_count: 1, open_case_count: 3,
  members: [
    { person_id: PERSON, role: "owner", started_at: "2026-01-05T09:00:00Z", ended_at: null },
    { person_id: "55555555-5555-4555-8555-555555555555", role: "viewer", started_at: "2026-02-05T09:00:00Z", ended_at: "2026-03-05T09:00:00Z" },
  ],
  invites: [{ id: INVITE, role: "expert", state: "expired", sent_at: "2026-10-01T09:00:00Z", expires_at: "2026-10-08T09:00:00Z" }],
  reassignments: [{ case_id: CASE_A, changed_at: "2026-10-20T09:00:00Z" }],
};

const staff = () => signedIn({ me: { ...me(), is_support_staff: true } });

test("a person look-up shows the account's state in plain words, with the cases that stop it closing", async () => {
  staff();
  let asked = "";
  server.use(
    http.get("/v1/support/people", ({ request }) => {
      asked = new URL(request.url).searchParams.get("q") ?? "";
      return json({ people: [person()] });
    }),
  );
  const { user, router } = renderAt("/support");
  const section = await screen.findByRole("region", { name: "Find a person" });
  await user.type(within(section).getByLabelText("Their sign-in email"), "kim@example.test");
  await user.click(within(section).getByRole("button", { name: "Look up person" }));
  expect(await within(section).findByText(/Locked out of the code step until/)).toBeInTheDocument();
  expect(asked).toBe("kim@example.test");
  expect(within(section).getByText("Authenticator set up.")).toBeInTheDocument();
  expect(within(section).getByText("Backup codes left: 2")).toBeInTheDocument();
  expect(within(section).getByText("Waiting for them to confirm a new email address.")).toBeInTheDocument();
  expect(within(section).getByText("No records custodian named.")).toBeInTheDocument();
  expect(within(section).getByText("Cases to export before the account can close: 1")).toBeInTheDocument();
  await user.click(within(section).getByRole("link", { name: CASE_A }));
  expect(router.state.location.pathname).toBe(`/support/cases/${CASE_A}`);
});

test("a person look-up that finds nobody says so", async () => {
  staff();
  server.use(http.get("/v1/support/people", () => json({ people: [] })));
  const { user } = renderAt("/support");
  const section = await screen.findByRole("region", { name: "Find a person" });
  await user.type(within(section).getByLabelText("Their sign-in email"), "nobody@example.test");
  await user.click(within(section).getByRole("button", { name: "Look up person" }));
  expect(await within(section).findByText("No person found.")).toBeInTheDocument();
});

test("an organisation look-up shows owners, open cases, members, invites and reassignments", async () => {
  staff();
  server.use(http.get(`/v1/support/organisations/${PERSONAL_ORG}`, () => json(organisation)));
  const { user } = renderAt("/support");
  const section = await screen.findByRole("region", { name: "Find a practice or team" });
  await user.type(within(section).getByLabelText("Practice or team number"), PERSONAL_ORG);
  await user.click(within(section).getByRole("button", { name: "Look up" }));
  expect(await within(section).findByText("Owners: 1 · Open cases: 3")).toBeInTheDocument();
  expect(within(section).getByText(/^Owner · since/)).toBeInTheDocument();
  expect(within(section).getByText(/^Viewer · left/)).toBeInTheDocument();
  expect(within(section).getByText(/^Invite as Expert: Expired/)).toBeInTheDocument();
  expect(within(section).getByText(/given to another expert/)).toBeInTheDocument();
});

test("the service check says what is working, and what a silent worker means", async () => {
  staff();
  server.use(
    http.get("/v1/support/cell/status", () =>
      json({ speech_ok: true, key_service_ok: false, worker_heartbeat_age_seconds: null, queue_depth: 4, paused_jobs: 1 }),
    ),
  );
  const { user } = renderAt("/support");
  const section = await screen.findByRole("region", { name: "How the service is running" });
  await user.click(within(section).getByRole("button", { name: "Check the service" }));
  expect(await within(section).findByText("Dictation: working")).toBeInTheDocument();
  expect(within(section).getByText("Key service: not set up")).toBeInTheDocument();
  expect(within(section).getByText("The worker has not checked in. Work waits until it does.")).toBeInTheDocument();
  expect(within(section).getByText("Work waiting: 4")).toBeInTheDocument();
  expect(within(section).getByText("Work paused: 1")).toBeInTheDocument();
  expect(within(section).getByText("Paused work waits safely; nothing is lost. It starts again when the service is back.")).toBeInTheDocument();
});

test("passes the automated accessibility checks with every answer shown", async () => {
  staff();
  server.use(
    http.get("/v1/support/people", () => json({ people: [person()] })),
    http.get(`/v1/support/organisations/${PERSONAL_ORG}`, () => json(organisation)),
    http.get("/v1/support/cell/status", () =>
      json({ speech_ok: true, key_service_ok: true, worker_heartbeat_age_seconds: 12, queue_depth: 0, paused_jobs: 0 }),
    ),
  );
  const { user, container } = renderAt("/support");
  const people = await screen.findByRole("region", { name: "Find a person" });
  await user.type(within(people).getByLabelText("Their sign-in email"), "kim@example.test");
  await user.click(within(people).getByRole("button", { name: "Look up person" }));
  const orgs = screen.getByRole("region", { name: "Find a practice or team" });
  await user.type(within(orgs).getByLabelText("Practice or team number"), PERSONAL_ORG);
  await user.click(within(orgs).getByRole("button", { name: "Look up" }));
  const cell = screen.getByRole("region", { name: "How the service is running" });
  await user.click(within(cell).getByRole("button", { name: "Check the service" }));
  await within(cell).findByText("Worker last checked in 12 seconds ago.");
  await within(orgs).findByText("Owners: 1 · Open cases: 3");
  expect(await axeViolations(container)).toEqual([]);
});
