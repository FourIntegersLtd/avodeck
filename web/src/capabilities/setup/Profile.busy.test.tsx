// S02: every action shows it is working and ignores a second click while it runs.
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, test, vi } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { casePack, combination, jurisdiction } from "./testing";

const ROLE = "e1111111-1111-4111-8111-111111111111";
const TRAINING = "a2222222-2222-4222-8222-222222222222";
const TEMPLATE = {
  id: "f1111111-1111-4111-8111-111111111111",
  filename: "Morgan template.docx",
  headings: [{ index: 1, style: "Heading 1", text: "Introduction" }],
  styles: ["Heading 1"],
  mapping: [],
  sections: ["introduction", "opinion"],
  style_details: [],
  sha256: "0".repeat(64),
  size_bytes: 2048,
  version: 1,
  created_at: "2026-10-01T09:00:00Z",
};
const tag = (body: object, etag = '"1"') => HttpResponse.json(body, { headers: { etag } });
const missing = () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 });
const ctx = { lineWidth: 0, lineCap: "", beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), clearRect: vi.fn() };

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((cb) => cb(new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" })));
});
afterEach(() => vi.restoreAllMocks());

function open(opts: { template?: boolean; training?: boolean } = {}) {
  const pack = casePack("launch");
  const specialty = combination("launch").specialty.code;
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", () =>
      tag({ version: 1, full_name: "Dr Jane Morgan", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1234567", specialties: [specialty], default_jurisdiction: jurisdiction("launch").code, default_retention: null, hourly_rate: null, currency: null }),
    ),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/me/roles", () => json([{ id: ROLE, version: 3, specialty, role_title: "Partner", setting: null, started_on: "2011-04-01", ended_on: null }])),
    http.get("/v1/me/training", () => json(opts.training ? [{ id: TRAINING, title: "Report writing course", year: 2021 }] : [])),
    http.get("/v1/me/template", () => (opts.template ? tag(TEMPLATE) : missing())),
    http.get("/v1/me/declaration", () => tag({ jurisdiction: jurisdiction("launch").code, wording: pack.declarations, additions: [], locked: false })),
    http.get("/v1/me/signature/image", missing),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
  );
  return renderAt("/profile");
}

const region = async (name: string) => within(await screen.findByRole("region", { name }));

test("saving your details goes busy and sends once", async () => {
  const h = hold();
  server.use(http.patch("/v1/me/profile", async () => (await h.enter(), tag({}, '"2"'))));
  const { user } = open();
  try {
    await user.click(await byName("Edit your details"));
    await user.type(await screen.findByLabelText("Full name"), "x");
    await expectBusyOnce(user, await byName("Save your details"), h);
  } finally { h.release(); }
});

test("adding a role goes busy and sends once", async () => {
  const h = hold();
  server.use(http.post("/v1/me/roles", async () => (await h.enter(), json({ id: "n", version: 1, specialty: "x", role_title: "Locum", setting: null, started_on: "2020-01-01", ended_on: null }, 201))));
  const { user } = open();
  try {
    const roles = await region("Where you've practised");
    await user.click(await roles.findByRole("button", { name: /Add another role/ }));
    await user.type(roles.getByLabelText("Your role"), "Locum");
    await user.type(roles.getByLabelText("From"), "2020-01-01");
    await expectBusyOnce(user, roles.getByRole("button", { name: "Add this role" }), h);
  } finally { h.release(); }
});

test("saving a role goes busy and sends once", async () => {
  const h = hold();
  server.use(http.patch(`/v1/me/roles/${ROLE}`, async () => (await h.enter(), tag({ id: ROLE, version: 4, specialty: "x", role_title: "Partner", setting: null, started_on: "2011-04-01", ended_on: null }, '"4"'))));
  const { user } = open();
  try {
    const roles = await region("Where you've practised");
    await user.click(await roles.findByRole("button", { name: "Edit Partner" }));
    await expectBusyOnce(user, roles.getByRole("button", { name: "Save this role" }), h);
  } finally { h.release(); }
});

test("removing a role goes busy and sends once", async () => {
  const h = hold();
  server.use(http.delete(`/v1/me/roles/${ROLE}`, async () => (await h.enter(), new HttpResponse(null, { status: 204 }))));
  const { user } = open();
  try {
    const roles = await region("Where you've practised");
    await user.click(await roles.findByRole("button", { name: "Edit Partner" }));
    await expectBusyOnce(user, roles.getByRole("button", { name: "Remove Partner" }), h);
  } finally { h.release(); }
});

test("adding a course goes busy and sends once", async () => {
  const h = hold();
  server.use(http.post("/v1/me/training", async () => (await h.enter(), json({ id: TRAINING, title: "Course", year: null }, 201))));
  const { user } = open();
  try {
    const training = await region("Expert training");
    await user.type(await training.findByLabelText("Course name"), "Course");
    await expectBusyOnce(user, training.getByRole("button", { name: "Add this course" }), h);
  } finally { h.release(); }
});

test("removing a course goes busy and sends once", async () => {
  const h = hold();
  server.use(http.delete(`/v1/me/training/${TRAINING}`, async () => (await h.enter(), new HttpResponse(null, { status: 204 }))));
  const { user } = open({ training: true });
  try {
    const training = await region("Expert training");
    await user.click(await training.findByRole("button", { name: "Edit your courses" }));
    await expectBusyOnce(user, training.getByRole("button", { name: "Remove Report writing course" }), h);
  } finally { h.release(); }
});

test("suggesting sections goes busy and sends once", async () => {
  const h = hold();
  server.use(http.post("/v1/me/template/suggestions", async () => (await h.enter(), json({ suggestions: [] }))));
  const { user } = open({ template: true });
  try {
    const template = await region("Your report template");
    await user.click(await template.findByRole("button", { name: "Match your headings to report sections" }));
    await expectBusyOnce(user, template.getByRole("button", { name: "Suggest sections" }), h);
  } finally { h.release(); }
});

test("saving the headings goes busy and sends once", async () => {
  const h = hold();
  server.use(http.patch("/v1/me/template/mapping", async () => (await h.enter(), tag(TEMPLATE, '"2"'))));
  const { user } = open({ template: true });
  try {
    const template = await region("Your report template");
    await user.click(await template.findByRole("button", { name: "Match your headings to report sections" }));
    await expectBusyOnce(user, template.getByRole("button", { name: "Save the headings" }), h);
  } finally { h.release(); }
});

test("saving what you added to the declaration goes busy and sends once", async () => {
  const h = hold();
  server.use(http.put("/v1/me/declaration", async () => (await h.enter(), tag({ jurisdiction: jurisdiction("launch").code, wording: casePack("launch").declarations, additions: [], locked: false }, '"2"'))));
  const { user } = open();
  try {
    const declaration = await region("Declaration and signature");
    await user.click(await declaration.findByRole("button", { name: "Review wording" }));
    await expectBusyOnce(user, await declaration.findByRole("button", { name: "Save what you added" }), h);
  } finally { h.release(); }
});

test("saving a drawn signature goes busy and sends once", async () => {
  const h = hold();
  server.use(http.post("/v1/me/signature", async () => (await h.enter(), json({}, 201))));
  const { user } = open();
  try {
    const declaration = await region("Declaration and signature");
    await user.click(await declaration.findByRole("button", { name: /Add your signature/ }));
    const pad = declaration.getByRole("img", { name: "Box to draw your signature in" });
    fireEvent.pointerDown(pad, { clientX: 10, clientY: 10 });
    fireEvent.pointerMove(pad, { clientX: 40, clientY: 30 });
    fireEvent.pointerUp(pad);
    await expectBusyOnce(user, declaration.getByRole("button", { name: "Save this signature" }), h);
  } finally { h.release(); }
});
