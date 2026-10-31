// S02 design match, the parts that needed server data: a role is edited in place with the version the list
// carried (If-Match), and the declaration tile says how many points the wording has and links to it.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, combination, jurisdiction, missingSteps } from "./testing";

const ROLE = "e1111111-1111-4111-8111-111111111111";
const tag = (body: object, etag = '"1"') => HttpResponse.json(body, { headers: { etag } });
const missing = () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 });

function open(points: number | null) {
  const pack = casePack("launch");
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", async () =>
      tag({ version: 1, full_name: "Dr Jane Morgan", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1234567", specialties: [combination("launch").specialty.code], default_jurisdiction: jurisdiction("launch").code, default_retention: null, hourly_rate: null, currency: null, missing_steps: await missingSteps(true) }),
    ),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/me/roles", () => json([{ id: ROLE, version: 3, specialty: combination("launch").specialty.code, role_title: "Partner", setting: null, started_on: "2011-04-01", ended_on: null }])),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/template", missing),
    http.get("/v1/me/declaration", () => tag({ jurisdiction: jurisdiction("launch").code, wording: pack.declarations.map((d) => ({ ...d, points })), additions: [], locked: false })),
    http.get("/v1/me/signature/image", missing),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
  );
}

test("Edit opens the role as a form, and saving sends the change with the role's version", async () => {
  open(null);
  let sent: { body: unknown; ifMatch: string | null } | undefined;
  server.use(
    http.patch(`/v1/me/roles/${ROLE}`, async ({ request }) => {
      sent = { body: await request.json(), ifMatch: request.headers.get("if-match") };
      return tag({ id: ROLE, version: 4, specialty: combination("launch").specialty.code, role_title: "Senior partner", setting: null, started_on: "2011-04-01", ended_on: "2024-06-30" }, '"4"');
    }),
  );
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  await user.click(within(roles).getByRole("button", { name: "Edit Partner" }));
  const title = within(roles).getByLabelText("Role");
  expect(title).toHaveValue("Partner");
  await user.clear(title);
  await user.type(title, "Senior partner");
  await user.type(within(roles).getByLabelText(/^Until/), "2024-06-30");
  await user.click(within(roles).getByRole("button", { name: "Save this role" }));
  await waitFor(() => expect(sent).toBeDefined());
  expect(sent).toEqual({ body: { role_title: "Senior partner", started_on: "2011-04-01", ended_on: "2024-06-30", setting: null }, ifMatch: '"3"' });
  expect(await within(roles).findByText("Senior partner")).toBeInTheDocument();
  expect(within(roles).getByText("2011 – 2024")).toBeInTheDocument();
});

test("a role changed elsewhere asks which version to keep instead of overwriting it", async () => {
  open(null);
  server.use(http.patch(`/v1/me/roles/${ROLE}`, () => HttpResponse.json({ error: { code: "stale" } }, { status: 412 })));
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  await user.click(within(roles).getByRole("button", { name: "Edit Partner" }));
  await user.click(within(roles).getByRole("button", { name: "Save this role" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.queryByText(/reload the page/i)).toBeNull();
});

test("the declaration tile counts the points and Review wording opens the wording", async () => {
  open(13);
  const { container, user } = renderAt("/profile");
  const declaration = await screen.findByRole("region", { name: "Declaration and signature" });
  expect(await within(declaration).findByText(/13-point declaration, ready to use/)).toBeInTheDocument();
  const pack = casePack("launch");
  const first = pack.declarations[0]!.text!.split("\n\n")[0]!;
  expect(within(declaration).queryByText(first)).toBeNull();
  const review = within(declaration).getByRole("button", { name: "Review wording" });
  expect(review).toHaveAttribute("aria-expanded", "false");
  await user.click(review);
  expect(review).toHaveAttribute("aria-expanded", "true");
  expect(within(declaration).getByText(first)).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("without a count the tile still invites a review", async () => {
  open(null);
  renderAt("/profile");
  const declaration = await screen.findByRole("region", { name: "Declaration and signature" });
  expect(await within(declaration).findByText(/ready to use/)).toBeInTheDocument();
  expect(within(declaration).queryByText(/-point declaration/)).toBeNull();
  expect(within(declaration).getByRole("button", { name: "Review wording" })).toBeInTheDocument();
});
