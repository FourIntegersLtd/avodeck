// S04 New case: when the organisation requires a report template it is used automatically and shown locked.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { casePack, combination, jurisdiction } from "./testing";

const TEAM = "7b1c0000-0000-4000-8000-000000000001";

function setup(locked: boolean) {
  const base = me();
  signedIn({
    me: {
      ...base,
      memberships: [
        ...base.memberships,
        { organisation_id: TEAM, name: "Carter Agency", kind: "team", role: "expert", started_at: "2026-01-05T09:00:00Z", member_count: 3 },
      ],
    },
    pack: casePack("launch"),
  });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/new-case/template", ({ request }) => {
      const org = new URL(request.url).searchParams.get("organisation_id");
      return json({
        organisation_id: org,
        required_template_id: org === TEAM && locked ? "c0de0000-0000-4000-8000-000000000009" : null,
        locked: org === TEAM && locked,
      });
    }),
  );
}

test("a team that requires a template shows it chosen and locked, in plain words", async () => {
  setup(true);
  renderAt("/cases/new");
  await userEvent.click(await screen.findByRole("radio", { name: "Carter Agency" }));
  expect(await screen.findByText("Carter Agency's report template is used for this case. You can't change it here.")).toBeInTheDocument();
});

test("no requirement, no template note", async () => {
  setup(false);
  renderAt("/cases/new");
  await userEvent.click(await screen.findByRole("radio", { name: "Carter Agency" }));
  await screen.findByRole("radio", { name: "Carter Agency", checked: true });
  expect(screen.queryByText(/report template is used for this case/)).not.toBeInTheDocument();
});
