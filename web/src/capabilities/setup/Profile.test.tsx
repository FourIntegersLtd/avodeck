// S02 Your expert profile: filled in once, used for every case. Roles carry dates (they are checked against
// each case's period), the declaration's pack wording is read-only, the template's headings are mapped to
// report sections (suggestions are never saved until confirmed) and the signature is drawn or uploaded.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, combination, jurisdiction, launchWords, type PackRun, missingSteps } from "./testing";

const ROLE = "e1111111-1111-4111-8111-111111111111";
const TEMPLATE = {
  id: "f1111111-1111-4111-8111-111111111111",
  filename: "Morgan template.docx",
  headings: [
    { index: 1, style: "Heading 1", text: "Introduction" },
    { index: 4, style: "Heading 1", text: "My opinion" },
  ],
  styles: ["Heading 1", "Normal"],
  mapping: [],
  sections: ["introduction", "opinion", "other"],
  sha256: "0".repeat(64),
  size_bytes: 2048,
  version: 1,
  created_at: "2026-10-01T09:00:00Z",
};

const withTag = (body: object, tag = '"1"', status = 200) => HttpResponse.json(body, { status, headers: { etag: tag } });

function profile(run: PackRun, opts: { template?: boolean } = {}) {
  const pack = casePack(run);
  const j = jurisdiction(run);
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", async () =>
      withTag({
        full_name: "Dr Jane Morgan",
        profession: pack.profession,
        registration_body: pack.registration_bodies[0],
        registration_number: "1234567",
        specialties: [combination(run).specialty.code],
        default_jurisdiction: j.code,
        default_retention: null,
        hourly_rate: null,
        currency: null,
        missing_steps: await missingSteps(true),
      }),
    ),
    http.get("/v1/packs", () => json([combination(run)])),
    http.get("/v1/jurisdictions", () => json([combination(run).jurisdiction])),
    http.get("/v1/jurisdictions/:code", () => json(j)),
    http.get("/v1/me/roles", () =>
      json([{ id: ROLE, specialty: combination(run).specialty.code, role_title: "Partner", setting: null, started_on: "2011-04-01", ended_on: null }]),
    ),
    http.get("/v1/me/training", () => json([{ id: "a2222222-2222-4222-8222-222222222222", title: "Report writing course", year: 2021 }])),
    http.get("/v1/me/template", () => (opts.template ? withTag(TEMPLATE) : HttpResponse.json({ error: { code: "not_found" } }, { status: 404 }))),
    http.get("/v1/me/declaration", () =>
      withTag({ jurisdiction: j.code, wording: pack.declarations, additions: [], locked: false }),
    ),
    http.get("/v1/me/signature/image", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/stats/balance", () => json({ counts: { other_side: 14, instructing_side: 9, joint: 2 }, months: 12 })),
  );
  return { pack, j };
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows the profile, roles with dates, training, the declaration and the balance, and passes axe", async () => {
    const { pack, j } = profile(run);
    const { container, user } = renderAt("/profile");
    expect(await screen.findByRole("heading", { level: 1, name: "Your expert profile" })).toBeInTheDocument();
    expect(await screen.findByText("Dr Jane Morgan")).toBeInTheDocument();
    expect(screen.getByText(`${pack.registration_bodies[0]} · 1234567`)).toBeInTheDocument();

    const roles = await screen.findByRole("region", { name: "Where you've practised" });
    expect(within(roles).getByText("Partner")).toBeInTheDocument();
    expect(within(roles).getByText("2011 – now")).toBeInTheDocument();
    const training = screen.getByRole("region", { name: "Expert training" });
    expect(await within(training).findByText("Report writing course")).toBeInTheDocument();

    // The pack's declaration wording is shown as it is and cannot be edited here.
    const declaration = screen.getByRole("region", { name: "Declaration and signature" });
    await user.click(within(declaration).getByRole("button", { name: "Review wording" }));
    const first = pack.declarations[0]!.text!.split("\n\n")[0]!;
    expect(await within(declaration).findByText(first)).toBeInTheDocument();
    expect(within(declaration).queryByDisplayValue(first)).toBeNull();

    // The balance uses the jurisdiction's party words.
    const words = j.vocabulary.party_roles as Record<string, string>;
    await user.click(screen.getByRole("button", { name: "See your balance over the last 12 months" }));
    const balance = await screen.findByRole("region", { name: "Your balance over the last 12 months" });
    expect(await within(balance).findByText(`for the ${words.other_side}`)).toBeInTheDocument();
    expect(within(balance).getByText("14")).toBeInTheDocument();

    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("saving your details sends only the change, with the version you loaded", async () => {
  profile("launch");
  let sent: { body: unknown; ifMatch: string | null } | undefined;
  server.use(
    http.patch("/v1/me/profile", async ({ request }) => {
      sent = { body: await request.json(), ifMatch: request.headers.get("if-match") };
      return withTag({}, '"2"');
    }),
  );
  const { user } = renderAt("/profile");
  await user.click(await screen.findByRole("button", { name: "Edit your details" }));
  const name = await screen.findByLabelText("Full name");
  await user.clear(name);
  await user.type(name, "Dr Jane E Morgan");
  await user.click(screen.getByRole("button", { name: "Save your details" }));
  await waitFor(() => expect(sent).toBeDefined());
  expect(sent!.ifMatch).toBe('"1"');
  expect(sent!.body).toMatchObject({ full_name: "Dr Jane E Morgan" });
  expect(await screen.findByText("Saved")).toBeInTheDocument();
});

test("adding a role sends its title and dates", async () => {
  profile("launch");
  let sent: unknown;
  server.use(
    http.post("/v1/me/roles", async ({ request }) => {
      sent = await request.json();
      return json({ id: "b3333333-3333-4333-8333-333333333333", specialty: "general-practice", role_title: "Locum", setting: null, started_on: "2013-01-01", ended_on: "2022-12-31" }, 201);
    }),
  );
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  await user.click(within(roles).getByRole("button", { name: "Add another role" }));
  await user.type(within(roles).getByLabelText("Your role"), "Locum");
  await user.type(within(roles).getByLabelText("From"), "2013-01-01");
  await user.type(within(roles).getByLabelText("To"), "2022-12-31");
  await user.click(within(roles).getByRole("button", { name: "Add this role" }));
  await waitFor(() => expect(sent).toEqual({ role_title: "Locum", specialty: "general-practice", started_on: "2013-01-01", ended_on: "2022-12-31" }));
});

test("suggested template sections are shown but saved only when you save the headings", async () => {
  profile("launch", { template: true });
  let patched: { body: unknown; ifMatch: string | null } | undefined;
  server.use(
    http.post("/v1/me/template/suggestions", () =>
      json({ suggestions: [{ index: 1, section: "introduction", reason: "opening heading" }, { index: 4, section: "opinion", reason: "opinion" }], rejected: 0 }),
    ),
    http.patch("/v1/me/template/mapping", async ({ request }) => {
      patched = { body: await request.json(), ifMatch: request.headers.get("if-match") };
      return withTag({ ...TEMPLATE, mapping: [] }, '"2"');
    }),
  );
  const { user } = renderAt("/profile");
  const template = await screen.findByRole("region", { name: "Your report template" });
  expect(await within(template).findByText("Morgan template.docx")).toBeInTheDocument();
  await user.click(within(template).getByRole("button", { name: "Match your headings to report sections" }));
  await user.click(within(template).getByRole("button", { name: "Suggest sections" }));
  const opinion = await within(template).findByLabelText("My opinion");
  await waitFor(() => expect(opinion).toHaveValue("opinion"));
  expect(patched).toBeUndefined();
  await user.click(within(template).getByRole("button", { name: "Save the headings" }));
  await waitFor(() => expect(patched).toBeDefined());
  expect(patched!.ifMatch).toBe('"1"');
  expect(patched!.body).toEqual({ mapping: [{ index: 1, section: "introduction" }, { index: 4, section: "opinion" }] });
});

test("uploading a Word template sends the file", async () => {
  profile("launch");
  let received: { filename: string | null; size: number } | undefined;
  server.use(
    http.post("/v1/me/template/file", async ({ request }) => {
      received = { filename: new URL(request.url).searchParams.get("filename"), size: (await request.arrayBuffer()).byteLength };
      return withTag(TEMPLATE, '"1"', 201);
    }),
  );
  const { user } = renderAt("/profile");
  const template = await screen.findByRole("region", { name: "Your report template" });
  const file = new File([new Uint8Array(2048)], "Morgan template.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
  await user.upload(within(template).getByLabelText("Choose your Word template"), file);
  await waitFor(() => expect(received).toEqual({ filename: "Morgan template.docx", size: 2048 }));
  await user.click(await within(template).findByRole("button", { name: "Match your headings to report sections" }));
  expect(await within(template).findByLabelText("Introduction")).toBeInTheDocument();
});

test("a photo of your signature is uploaded as a picture", async () => {
  profile("launch");
  let method: string | null = null;
  server.use(
    http.post("/v1/me/signature", ({ request }) => {
      method = new URL(request.url).searchParams.get("method");
      return json({ id: "c4444444-4444-4444-8444-444444444444", method: "photo", width: 300, height: 100, created_at: "2026-10-17T09:00:00Z" }, 201);
    }),
  );
  const { user } = renderAt("/profile");
  const declaration = await screen.findByRole("region", { name: "Declaration and signature" });
  await user.click(within(declaration).getByRole("button", { name: /Add your signature/ }));
  await user.upload(within(declaration).getByLabelText("Upload a photo of your signature"), new File([new Uint8Array(64)], "sig.png", { type: "image/png" }));
  await waitFor(() => expect(method).toBe("photo"));
  expect(await within(declaration).findByText("Your signature is saved.")).toBeInTheDocument();
});
