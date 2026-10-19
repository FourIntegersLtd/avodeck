// S02 design match: a progress ring in the banner, the template's styles shown as chips with a Replace
// link, the two halves (training and template) side by side, and a Done footer.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, combination, jurisdiction } from "./testing";

const TEMPLATE = {
  id: "f1111111-1111-4111-8111-111111111111",
  filename: "Morgan template.docx",
  headings: [],
  styles: ["Heading 1", "Normal"],
  mapping: [],
  sections: ["introduction", "other"],
  sha256: "0".repeat(64),
  size_bytes: 2048,
  version: 1,
  created_at: "2026-10-01T09:00:00Z",
};
const tag = (body: object) => HttpResponse.json(body, { headers: { etag: '"1"' } });
const missing = () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 });

function open() {
  const pack = casePack("launch");
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", () =>
      tag({ full_name: "Dr Jane Morgan", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1234567", specialties: [combination("launch").specialty.code], default_jurisdiction: jurisdiction("launch").code, default_retention: null, hourly_rate: null, currency: null }),
    ),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/me/roles", () => json([{ id: "e1111111-1111-4111-8111-111111111111", specialty: "x", role_title: "Partner", setting: null, started_on: "2011-04-01", ended_on: null }])),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/template", () => tag(TEMPLATE)),
    http.get("/v1/me/declaration", () => tag({ jurisdiction: jurisdiction("launch").code, wording: pack.declarations, additions: [], locked: false })),
    http.get("/v1/me/signature/image", missing),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
  );
}

test("the banner shows how many of the five parts are done as a ring", async () => {
  open();
  renderAt("/profile");
  const ring = await screen.findByRole("img", { name: "3 of 5 parts done" });
  expect(ring).toBeInTheDocument();
});

test("the progress sentence is shown once, in the banner", async () => {
  open();
  renderAt("/profile");
  await screen.findByRole("img", { name: "3 of 5 parts done" });
  expect(screen.getAllByText("3 of 5 parts done")).toHaveLength(1);
});

test("the template shows its styles as chips and a Replace link, and a Done button closes the page", async () => {
  open();
  const { container } = renderAt("/profile");
  const template = await screen.findByRole("region", { name: "Your report template" });
  expect(within(template).getByText("Heading 1")).toBeInTheDocument();
  expect(within(template).getByText("Normal")).toBeInTheDocument();
  expect(within(template).getByText("Choose a different Word template")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Done" })).toHaveAttribute("href", "/today");
  expect(await axeViolations(container)).toEqual([]);
});
