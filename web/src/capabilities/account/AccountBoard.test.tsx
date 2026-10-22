// S28 against its board (docs/design/screens/S28-Account.dc.html): "Change" and "Sign out" are text
// links, your organisations are listed from /v1/me, backup codes read "For when you lose your phone.
// 7 of 10 left. Keep them somewhere safe…", and signing out sits on this device's row.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { device, me, PERSONAL_ORG, TEAM_ORG } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const OTHER = "77777777-7777-4777-8777-777777777777";

function account() {
  const person = me();
  signedIn({
    me: {
      ...person,
      memberships: [
        ...person.memberships,
        { organisation_id: TEAM_ORG, name: "Northgate", kind: "team", role: "admin", started_at: "2026-02-01T09:00:00Z", member_count: 1 },
      ],
    },
  });
  server.use(
    http.get("/v1/me/sessions", () =>
      json([device(), device({ id: OTHER, device_label: "Safari on iPad", current: false, last_seen_at: new Date(Date.now() - 2 * 86_400_000).toISOString() })]),
    ),
    http.get("/v1/auth/mfa/recovery-codes", () => json({ remaining: 7, total: 10 })),
  );
}

test("email and password say Change, as text links that still name what they change", async () => {
  account();
  renderAt("/account");
  const email = await screen.findByRole("button", { name: "Change email" });
  const password = screen.getByRole("button", { name: "Change password" });
  for (const link of [email, password]) {
    expect(link).toHaveClass("av-button--quiet");
    expect(link.firstChild?.textContent).toBe("Change");
  }
  expect(screen.getByText("Email")).toBeInTheDocument();
});

test("backup codes read like the board", async () => {
  account();
  renderAt("/account");
  expect(await screen.findByText("For when you lose your phone. 7 of 10 left. Keep them somewhere safe, away from your phone.")).toBeInTheDocument();
});

test("your organisations are listed with your role in each", async () => {
  account();
  renderAt("/account");
  const orgs = await screen.findByRole("region", { name: "Your organisations" });
  const rows = within(orgs).getAllByRole("listitem");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("My practice");
  expect(rows[0]).toHaveTextContent("Owner");
  expect(rows[1]).toHaveTextContent("Northgate");
  expect(rows[1]).toHaveTextContent("Admin");
  expect(PERSONAL_ORG).toBeTruthy();
});

test("signing out sits on this device's row; other devices have a Sign out text link", async () => {
  account();
  renderAt("/account");
  const devices = await screen.findByRole("region", { name: "Where you're signed in" });
  const rows = await within(devices).findAllByRole("listitem");
  expect(within(rows[0]!).getByRole("button", { name: "Sign out" })).toHaveClass("av-button--quiet");
  const other = within(rows[1]!).getByRole("button", { name: "Sign out of Safari on iPad" });
  expect(other).toHaveClass("av-button--quiet");
  expect(other.firstChild?.textContent).toBe("Sign out");
});
