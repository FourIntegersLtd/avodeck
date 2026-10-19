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

test("the template shows its styles as chips and a Replace link, and a Done button closes the page", async () => {
  open();
  const { container } = renderAt("/profile");
  const template = await screen.findByRole("region", { name: "Your report template" });
  expect(within(template).getByText("Heading 1")).toBeInTheDocument();
  expect(within(template).getByText("Normal")).toBeInTheDocument();
  expect(within(template).getByText("Replace")).toBeInTheDocument();
  expect(within(template).getByText("Your headings, numbering and fonts will be kept")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Done" })).toHaveAttribute("href", "/today");
  expect(await axeViolations(container)).toEqual([]);
});

test("about you is a summary of name, registration and specialties, and Edit your details opens the whole form", async () => {
  open();
  const pack = casePack("launch");
  const { user } = renderAt("/profile");
  const about = await screen.findByRole("region", { name: "About you" });
  expect(within(about).getByText("Dr Jane Morgan")).toBeInTheDocument();
  expect(within(about).getByText(`${pack.registration_bodies[0]} · 1234567`)).toBeInTheDocument();
  expect(within(about).getByText("Specialty A")).toBeInTheDocument();
  expect(within(about).queryByLabelText("Profession")).toBeNull();
  await user.click(within(about).getByRole("button", { name: "Edit your details" }));
  for (const label of ["Full name", "Profession", "Registered with", "Registration number"]) expect(within(about).getByLabelText(label)).toBeInTheDocument();
  expect(within(about).getByRole("checkbox", { name: "Specialty A" })).toBeChecked();
});

test("the banner says how many parts are done and what is next, and the footer says Done only after something is saved", async () => {
  open();
  renderAt("/profile");
  expect(await screen.findByText("3 of 5 done.")).toBeInTheDocument();
  expect(screen.getByText("Next: add your expert training.")).toBeInTheDocument();
  expect(screen.queryByText("All changes saved")).toBeNull();
});

test("the last card names Account and security and carries the balance behind a link", async () => {
  open();
  renderAt("/profile");
  expect(await screen.findByRole("heading", { name: "This profile goes with you" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Account and security" })).toHaveAttribute("href", "/account");
  expect(screen.queryByRole("region", { name: "Your balance over the last 12 months" })).toBeNull();
});

test("the signature is a one-line bar until you open it, and the role rows read Edit", async () => {
  open();
  const { user } = renderAt("/profile");
  const declaration = await screen.findByRole("region", { name: "Declaration and signature" });
  expect(within(declaration).queryByRole("img", { name: "Box to draw your signature in" })).toBeNull();
  const bar = within(declaration).getByRole("button", { name: /Add your signature/ });
  expect(bar).toHaveTextContent("Sign with your mouse, or upload a photo of it.");
  await user.click(bar);
  expect(within(declaration).getByRole("img", { name: "Box to draw your signature in" })).toBeInTheDocument();
  const roles = screen.getByRole("region", { name: "Where you've practised" });
  expect(within(roles).getByRole("button", { name: "Edit Partner" })).toHaveTextContent(/^Edit$/);
  expect(within(roles).getByRole("button", { name: "Add another role" })).toBeInTheDocument();
});
