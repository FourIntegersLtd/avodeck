// S28 with the data the board shows: "Changed 3 months ago", "Owner · just you" / "Admin · 4 people",
// and Settings and Leave on each organisation, which open the screens slice O registers.
import { screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../../foundation/lib/screens";
import { http, json, server, signedIn } from "../../test/api";
import { me, PERSONAL_ORG, TEAM_ORG } from "../../test/fixtures";
import { renderAt } from "../../test/render";

afterEach(() => resetScreens());

function account(over: Parameters<typeof me>[0] = {}) {
  const person = me(over);
  signedIn({
    me: {
      ...person,
      memberships: [
        { ...person.memberships[0]!, member_count: 1 },
        { organisation_id: TEAM_ORG, name: "Northgate Medico-Legal", kind: "team", role: "admin", started_at: "2026-02-01T09:00:00Z", member_count: 4 },
      ],
    },
  });
  server.use(http.get("/v1/me/sessions", () => json([])));
}

test("the password row says when it was last changed", async () => {
  account({ password_changed_at: new Date(Date.now() - 95 * 86_400_000).toISOString() });
  renderAt("/account");
  expect(await screen.findByText("Changed 3 months ago")).toBeInTheDocument();
});

test("the password row says nothing about a change that never happened", async () => {
  account({ password_changed_at: null });
  renderAt("/account");
  await screen.findByRole("button", { name: "Change password" });
  expect(screen.queryByText(/^Changed /)).toBeNull();
});

test("each organisation says your role and how many people are in it", async () => {
  account();
  renderAt("/account");
  const orgs = await screen.findByRole("region", { name: "Your organisations" });
  const rows = within(orgs).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("Owner · just you");
  expect(rows[1]).toHaveTextContent("Admin · 4 people");
});

test("Settings and Leave appear when slice O's screens are registered; a personal organisation cannot be left", async () => {
  registerScreen("organisation_settings", ({ params }) => `/organisations/${String(params.organisation_id)}`);
  registerScreen("organisation_leave", ({ params }) => `/organisations/${String(params.organisation_id)}/leave`);
  account();
  renderAt("/account");
  const orgs = await screen.findByRole("region", { name: "Your organisations" });
  const [mine, team] = within(orgs).getAllByRole("listitem");
  expect(within(mine!).getByRole("link", { name: "Settings for My practice" })).toHaveAttribute("href", `/organisations/${PERSONAL_ORG}`);
  expect(within(mine!).queryByRole("link", { name: /^Leave/ })).toBeNull();
  expect(within(team!).getByRole("link", { name: "Settings for Northgate Medico-Legal" })).toHaveAttribute("href", `/organisations/${TEAM_ORG}`);
  expect(within(team!).getByRole("link", { name: "Leave Northgate Medico-Legal" })).toHaveAttribute("href", `/organisations/${TEAM_ORG}/leave`);
});

test("with no organisation screens registered there are no dead links", async () => {
  account();
  renderAt("/account");
  const orgs = await screen.findByRole("region", { name: "Your organisations" });
  expect(within(orgs).queryAllByRole("link")).toHaveLength(0);
});
