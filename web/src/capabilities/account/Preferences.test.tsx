// S28 "How we tell you things": per organisation, in the app, by email, one email a day.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { device, PERSONAL_ORG, TEAM_ORG } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

const choices = [
  { organisation_id: PERSONAL_ORG, organisation_name: "Jane Morgan", organisation_kind: "personal", in_app: true, email: true, digest: false, muted_kinds: [] },
  { organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team", in_app: true, email: true, digest: false, muted_kinds: ["zz.ready"] },
];

function account() {
  signedIn();
  server.use(
    http.get("/v1/me/sessions", () => json([device()])),
    http.get("/v1/me/notification-preferences", () => json(choices)),
  );
}

test("shows each organisation's choices and says emails carry no patient details", async () => {
  account();
  const { container } = renderAt("/account");
  const section = await screen.findByRole("region", { name: "How we tell you things" });
  expect(within(section).getByText("Emails never contain patient details, just a link back here.")).toBeInTheDocument();
  const mine = await within(section).findByRole("group", { name: "My practice" });
  expect(within(mine).getByRole("checkbox", { name: "In the app" })).toBeChecked();
  expect(within(mine).getByRole("checkbox", { name: "By email" })).toBeChecked();
  expect(within(mine).getByRole("checkbox", { name: "One email a day" })).not.toBeChecked();
  expect(within(section).getByRole("group", { name: "Northgate" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("changing a choice saves that organisation's choices and keeps muted kinds", async () => {
  account();
  let sent: unknown = null;
  server.use(
    http.put("/v1/me/notification-preferences", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json(sent as never);
    }),
  );
  const { user } = renderAt("/account");
  const team = await screen.findByRole("group", { name: "Northgate" });
  await user.click(within(team).getByRole("checkbox", { name: "One email a day" }));
  await waitFor(() =>
    expect(sent).toEqual([{ organisation_id: TEAM_ORG, in_app: true, email: true, digest: true, muted_kinds: ["zz.ready"] }]),
  );
  expect(within(team).getByRole("checkbox", { name: "One email a day" })).toBeChecked();
});
