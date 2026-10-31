// Start a team: one screen, one main action; the creator lands on the new team's settings.
import { screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { accountSections } from "../../foundation/lib/slots";
import { screenPath } from "../../foundation/lib/screens";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { org, TEAM_ORG, teamWorld } from "./testing";
import "./index";

vi.setConfig({ testTimeout: 30_000 });

test("the screen key points at /organisations/new", () => {
  expect(screenPath("organisation_new")).toBe("/organisations/new");
});

test("/organisations/new is the start-a-team screen, not the settings screen for a team called 'new'", async () => {
  signedIn();
  const { container } = renderAt("/organisations/new");
  expect(await screen.findByRole("heading", { level: 1, name: "Start a team" })).toBeInTheDocument();
  expect(screen.getByLabelText("Team name")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start the team" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("the account page offers a Start a team section, above the custodian", async () => {
  const keys = accountSections().map((s) => s.key);
  expect(keys).toContain("organisations.start_team");
  expect(keys.indexOf("organisations.start_team")).toBeLessThan(keys.indexOf("organisations.custodian"));
  signedIn();
  server.use(http.get("/v1/me/sessions", () => json([])), http.get("/v1/me/close", () => json({ cases: [] })));
  const { user, router } = renderAt("/account");
  await user.click(await screen.findByRole("link", { name: "Start a team" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/organisations/new"));
});

test("starting a team sends the trimmed name and opens the new team's settings", async () => {
  teamWorld();
  const bodies: unknown[] = [];
  server.use(
    http.post("/v1/organisations", async ({ request }) => {
      bodies.push(await request.json());
      return json(org(), 201);
    }),
  );
  const { user, router } = renderAt("/organisations/new");
  await user.type(await screen.findByLabelText("Team name"), "  Northgate Medico-Legal  ");
  await user.click(screen.getByRole("button", { name: "Start the team" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/organisations/${TEAM_ORG}`));
  expect(bodies).toEqual([{ name: "Northgate Medico-Legal" }]);
});

test("an empty name is asked for and nothing is sent", async () => {
  signedIn();
  let sent = 0;
  server.use(http.post("/v1/organisations", () => { sent += 1; return json(org(), 201); }));
  const { user, router } = renderAt("/organisations/new");
  await user.type(await screen.findByLabelText("Team name"), "   ");
  await user.click(screen.getByRole("button", { name: "Start the team" }));
  expect(await screen.findByText("Type a name for your team.")).toBeInTheDocument();
  expect(sent).toBe(0);
  expect(router.state.location.pathname).toBe("/organisations/new");
});

test("a server refusal is shown in plain words and the person stays", async () => {
  signedIn();
  server.use(http.post("/v1/organisations", () => apiError(422, "validation_error")));
  const { user, router } = renderAt("/organisations/new");
  await user.type(await screen.findByLabelText("Team name"), "Northgate");
  await user.click(screen.getByRole("button", { name: "Start the team" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/organisations/new");
});

test("the button is busy while the request is held and a second click sends one request", async () => {
  signedIn();
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let sent = 0;
  server.use(http.post("/v1/organisations", async () => { sent += 1; await gate; return json(org(), 201); }));
  const { user } = renderAt("/organisations/new");
  await user.type(await screen.findByLabelText("Team name"), "Northgate");
  const button = screen.getByRole("button", { name: "Start the team" });
  await user.click(button);
  await user.click(button);
  expect(sent).toBe(1);
  release();
});
