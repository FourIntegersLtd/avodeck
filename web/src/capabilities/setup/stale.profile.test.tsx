// The template-mapping and declaration-additions saves also ask which version to keep when they find the
// thing changed elsewhere (412), and keep what the expert typed.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { casePack, combination, jurisdiction, missingSteps } from "./testing";

const tag = (body: object, etag: string) => HttpResponse.json(body, { headers: { etag } });
const stale = () => HttpResponse.json({ error: { code: "stale" } }, { status: 412 });
const KEEP_MINE = "Keep what I wrote here";
const USE_SAVED = "Keep the saved version";
const TEMPLATE = {
  id: "f1111111-1111-4111-8111-111111111111",
  filename: "Morgan template.docx",
  headings: [{ index: 1, style: "Heading 1", text: "Introduction" }, { index: 4, style: "Heading 1", text: "My opinion" }],
  styles: ["Heading 1"],
  mapping: [] as Array<{ index: number; section: string }>,
  sections: ["introduction", "opinion", "other"],
  sha256: "0".repeat(64),
  size_bytes: 2048,
  version: 1,
  created_at: "2026-10-01T09:00:00Z",
};

function open(over: { template?: object; declaration?: (n: number) => object }) {
  const pack = casePack("launch");
  const j = jurisdiction("launch");
  signedIn({ me: me(), pack });
  let declGets = 0;
  server.use(
    http.get("/v1/me/profile", async () => tag({ version: 1, full_name: "Dr Jane Morgan", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1", specialties: [combination("launch").specialty.code], default_jurisdiction: j.code, default_retention: null, hourly_rate: null, currency: null, missing_steps: await missingSteps(true) }, '"1"')),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions", () => json([combination("launch").jurisdiction])),
    http.get("/v1/jurisdictions/:code", () => json(j)),
    http.get("/v1/me/roles", () => json([])),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/signature/image", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
    http.get("/v1/me/template", () => (over.template ? over.template && tag(TEMPLATE, '"1"') : HttpResponse.json({ error: { code: "not_found" } }, { status: 404 }))),
    http.get("/v1/me/declaration", () => {
      const n = ++declGets;
      return tag({ jurisdiction: j.code, wording: pack.declarations, additions: [], locked: false, ...(over.declaration?.(n) ?? {}) }, n === 1 ? '"1"' : '"9"');
    }),
  );
  return pack;
}

test("template headings: keep mine saves my matches against the latest version", async () => {
  open({ template: TEMPLATE });
  const sent: Array<{ body: unknown; ifMatch: string | null }> = [];
  server.use(
    http.post("/v1/me/template/suggestions", () => json({ suggestions: [{ index: 1, section: "introduction", reason: "x" }, { index: 4, section: "opinion", reason: "x" }], rejected: 0 })),
    http.patch("/v1/me/template/mapping", async ({ request }) => {
      sent.push({ body: await request.json(), ifMatch: request.headers.get("if-match") });
      return sent.length === 1 ? stale() : tag(TEMPLATE, '"10"');
    }),
  );
  // the refetch after the 412 sees a newer saved copy
  let gets = 0;
  server.use(http.get("/v1/me/template", () => tag({ ...TEMPLATE, mapping: ++gets > 1 ? [{ index: 1, section: "other" }] : [] }, gets > 1 ? '"8"' : '"1"')));
  const { user } = renderAt("/profile");
  const template = await screen.findByRole("region", { name: "Your report template" });
  await user.click(await within(template).findByRole("button", { name: "Match your headings to report sections" }));
  await user.click(within(template).getByRole("button", { name: "Suggest sections" }));
  await waitFor(() => expect(within(template).getByLabelText("My opinion")).toHaveValue("opinion"));
  await user.click(within(template).getByRole("button", { name: "Save the headings" }));
  const choice = await screen.findByRole("dialog");
  expect(screen.queryByText(/reload the page/i)).toBeNull();
  await user.click(within(choice).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.ifMatch).toBe('"8"');
  expect(sent[1]!.body).toEqual({ mapping: [{ index: 1, section: "introduction" }, { index: 4, section: "opinion" }] });
});

test("declaration additions: the choice shows both, and keep the saved one sends nothing more", async () => {
  const pack = open({ declaration: (n) => (n > 1 ? { additions: [{ kind: pack0(), text: "Added by someone else" }] } : {}) });
  function pack0() {
    return String(pack.declarations[0]!.key);
  }
  let puts = 0;
  server.use(http.put("/v1/me/declaration", () => (++puts, stale())));
  const { user } = renderAt("/profile");
  const decl = await screen.findByRole("region", { name: "Declaration and signature" });
  await user.click(await within(decl).findByRole("button", { name: "Review wording" }));
  await user.type((await within(decl).findAllByLabelText("Anything you want to add"))[0]!, "My own addition");
  await user.click(within(decl).getByRole("button", { name: "Save what you added" }));
  const choice = await screen.findByRole("dialog");
  expect(within(choice).getByText(/My own addition/)).toBeInTheDocument();
  expect(within(choice).getByText(/Added by someone else/)).toBeInTheDocument();
  await user.click(within(choice).getByRole("button", { name: USE_SAVED }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(puts).toBe(1);
  expect(await within(decl).findByDisplayValue("Added by someone else")).toBeInTheDocument();
});

test("declaration additions: keep mine saves again with the latest version", async () => {
  const pack = open({ declaration: (n) => (n > 1 ? { additions: [{ kind: String(casePack("launch").declarations[0]!.key), text: "Added by someone else" }] } : {}) });
  void pack;
  const sent: Array<{ body: unknown; ifMatch: string | null }> = [];
  server.use(
    http.put("/v1/me/declaration", async ({ request }) => {
      sent.push({ body: await request.json(), ifMatch: request.headers.get("if-match") });
      return sent.length === 1 ? stale() : tag({ jurisdiction: "x", wording: casePack("launch").declarations, additions: [], locked: false }, '"10"');
    }),
  );
  const { user } = renderAt("/profile");
  const decl = await screen.findByRole("region", { name: "Declaration and signature" });
  await user.click(await within(decl).findByRole("button", { name: "Review wording" }));
  await user.type((await within(decl).findAllByLabelText("Anything you want to add"))[0]!, "My own addition");
  await user.click(within(decl).getByRole("button", { name: "Save what you added" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.ifMatch).toBe('"9"');
  expect(JSON.stringify(sent[1]!.body)).toContain("My own addition");
});
