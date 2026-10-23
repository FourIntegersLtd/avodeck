// Invite accept page, in the sign-in layout (S01).
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { TEAM_ORG } from "./testing";

const TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789";

test("joins the team once the person confirms", async () => {
  signedIn();
  let body = 0;
  server.use(
    http.post(`/v1/invites/${TOKEN}/accept`, () => {
      body += 1;
      return json({ organisation_id: TEAM_ORG, role: "expert", membership_id: TEAM_ORG });
    }),
  );
  const { user } = renderAt(`/invites/${TOKEN}`);
  expect(await screen.findByRole("heading", { level: 1, name: "Join a team on Avodeck" })).toBeInTheDocument();
  expect(body).toBe(0);
  await user.click(screen.getByRole("button", { name: "Join the team" }));
  expect(await screen.findByText("You have joined the team as an expert.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open the team" })).toHaveAttribute("href", `/organisations/${TEAM_ORG}`);
});

test("a link that cannot be used says so in plain words", async () => {
  signedIn();
  server.use(http.post(`/v1/invites/${TOKEN}/accept`, () => apiError(404, "not_found")));
  const { user } = renderAt(`/invites/${TOKEN}`);
  await user.click(await screen.findByRole("button", { name: "Join the team" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This invite can't be used.");
});

test("someone who is not signed in is sent to sign in, and comes back here", async () => {
  server.use(http.post(`/v1/invites/${TOKEN}/accept`, () => apiError(401, "not_signed_in")), http.get("/v1/auth/session", () => apiError(401, "not_signed_in")), http.get("/v1/cell", () => json({ storage_place: null, support_phone: null, support_hours: null })));
  const { user, router } = renderAt(`/invites/${TOKEN}`);
  await user.click(await screen.findByRole("button", { name: "Join the team" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
  expect(router.state.location.state).toMatchObject({ from: `/invites/${TOKEN}` });
});

test("passes the automated accessibility checks", async () => {
  signedIn();
  const { container } = renderAt(`/invites/${TOKEN}`);
  await screen.findByRole("button", { name: "Join the team" });
  expect(await axeViolations(container)).toEqual([]);
});
