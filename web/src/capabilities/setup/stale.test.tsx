// A save that finds the thing changed elsewhere (412) keeps what the expert typed and asks which version to
// keep, instead of telling them to reload the page. The latest saved version is fetched for the choice.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_A, me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, casePack, combination, inCase, jurisdiction, question, questions } from "./testing";

const ROLE = "e1111111-1111-4111-8111-111111111111";
const tag = (body: object, etag: string) => HttpResponse.json(body, { headers: { etag } });
const stale = () => HttpResponse.json({ error: { code: "stale" } }, { status: 412 });
const KEEP_MINE = "Keep what I wrote here";
const USE_SAVED = "Keep the saved version";

function profile() {
  const pack = casePack("launch");
  signedIn({ me: me(), pack });
  const p = (name: string) => ({ version: 1, full_name: name, profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1234567", specialties: [combination("launch").specialty.code], default_jurisdiction: jurisdiction("launch").code, default_retention: null, hourly_rate: null, currency: null, missing_steps: ["training", "template", "signature"] });
  const role = (title: string, version: number) => ({ id: ROLE, version, specialty: combination("launch").specialty.code, role_title: title, setting: null, started_on: "2011-04-01", ended_on: null });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions", () => json([combination("launch").jurisdiction])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/template", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/declaration", () => tag({ jurisdiction: jurisdiction("launch").code, wording: pack.declarations, additions: [], locked: false }, '"1"')),
    http.get("/v1/me/signature/image", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
  );
  return { p, role };
}

test("your details: keep mine saves my words against the latest version", async () => {
  const { p, role } = profile();
  let gets = 0;
  const sent: Array<{ body: unknown; ifMatch: string | null }> = [];
  server.use(
    http.get("/v1/me/roles", () => json([role("Partner", 3)])),
    http.get("/v1/me/profile", () => (++gets === 1 ? tag(p("Dr Jane Morgan"), '"1"') : tag(p("Dr J Morgan-Smith"), '"5"'))),
    http.patch("/v1/me/profile", async ({ request }) => {
      sent.push({ body: await request.json(), ifMatch: request.headers.get("if-match") });
      return sent.length === 1 ? stale() : tag({}, '"6"');
    }),
  );
  const { user } = renderAt("/profile");
  await user.click(await screen.findByRole("button", { name: "Edit your details" }));
  const name = await screen.findByLabelText("Full name");
  await user.clear(name);
  await user.type(name, "Dr Jane E Morgan");
  await user.click(screen.getByRole("button", { name: "Save your details" }));
  const choice = await screen.findByRole("dialog");
  expect(within(choice).getByText(/Dr Jane E Morgan/)).toBeInTheDocument();
  expect(within(choice).getByText(/Dr J Morgan-Smith/)).toBeInTheDocument();
  expect(screen.queryByText(/reload the page/i)).toBeNull();
  await user.click(within(choice).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.ifMatch).toBe('"5"');
  expect(sent[1]!.body).toMatchObject({ full_name: "Dr Jane E Morgan" });
  expect(await screen.findByText("Saved")).toBeInTheDocument();
});

test("a role: keep the saved version drops mine and shows the saved one", async () => {
  const { p, role } = profile();
  let gets = 0;
  let patches = 0;
  server.use(
    http.get("/v1/me/profile", () => tag(p("Dr Jane Morgan"), '"1"')),
    http.get("/v1/me/roles", () => json([++gets === 1 ? role("Partner", 3) : role("Managing partner", 7)])),
    http.patch(`/v1/me/roles/${ROLE}`, () => (++patches, stale())),
  );
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  await user.click(within(roles).getByRole("button", { name: "Edit Partner" }));
  const title = within(roles).getByLabelText("Role");
  await user.clear(title);
  await user.type(title, "Senior partner");
  await user.click(within(roles).getByRole("button", { name: "Save this role" }));
  const choice = await screen.findByRole("dialog");
  expect(within(choice).getByText(/Senior partner/)).toBeInTheDocument();
  expect(within(choice).getByText(/Managing partner/)).toBeInTheDocument();
  await user.click(within(choice).getByRole("button", { name: USE_SAVED }));
  expect(await within(roles).findByText("Managing partner")).toBeInTheDocument();
  expect(patches).toBe(1);
});

test("a role: keep mine sends it again with the latest version", async () => {
  const { p, role } = profile();
  let gets = 0;
  const sent: Array<string | null> = [];
  server.use(
    http.get("/v1/me/profile", () => tag(p("Dr Jane Morgan"), '"1"')),
    http.get("/v1/me/roles", () => json([++gets === 1 ? role("Partner", 3) : role("Managing partner", 7)])),
    http.patch(`/v1/me/roles/${ROLE}`, ({ request }) => {
      sent.push(request.headers.get("if-match"));
      return sent.length === 1 ? stale() : tag(role("Senior partner", 8), '"8"');
    }),
  );
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  await user.click(within(roles).getByRole("button", { name: "Edit Partner" }));
  const title = within(roles).getByLabelText("Role");
  await user.clear(title);
  await user.type(title, "Senior partner");
  await user.click(within(roles).getByRole("button", { name: "Save this role" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toEqual(['"3"', '"7"']));
  expect(await within(roles).findByText("Senior partner")).toBeInTheDocument();
});

test("case details: keep mine saves my name for the case against the latest version", async () => {
  inCase("launch");
  let gets = 0;
  const sent: Array<{ body: unknown; ifMatch: string | null }> = [];
  server.use(
    http.get("/v1/cases/:caseId", () => (++gets <= 1 ? tag(caseItem("launch", { due_date: null }), '"3"') : tag(caseItem("launch", { due_date: null, label: "Changed elsewhere" }), '"6"'))),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
    http.patch("/v1/cases/:caseId", async ({ request }) => {
      sent.push({ body: await request.json(), ifMatch: request.headers.get("if-match") });
      return sent.length === 1 ? stale() : tag(caseItem("launch", { label: "My name" }), '"7"');
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const label = await screen.findByLabelText("A short name for the case");
  await user.clear(label);
  await user.type(label, "My name");
  await user.click(screen.getByRole("button", { name: "Save case details" }));
  const choice = await screen.findByRole("dialog");
  expect(within(choice).getByText(/My name/)).toBeInTheDocument();
  expect(within(choice).getByText(/Changed elsewhere/)).toBeInTheDocument();
  await user.click(within(choice).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.ifMatch).toBe('"6"');
  expect(sent[1]!.body).toMatchObject({ label: "My name" });
});

test("a question's wording: the choice shows both and keep mine sends the latest version", async () => {
  inCase("launch");
  const kind = String(casePack("launch").question_types[0]!.key);
  let gets = 0;
  const sent: Array<{ body: unknown; ifMatch: string | null }> = [];
  server.use(
    http.get("/v1/cases/:caseId/questions", () =>
      json(questions({ questions: [++gets === 1 ? question({ kind }) : question({ kind, version: 4, confirmed_text: "Reworded by someone else" })] })),
    ),
    http.patch("/v1/cases/:caseId/questions/:id", async ({ request }) => {
      sent.push({ body: await request.json(), ifMatch: request.headers.get("if-match") });
      return sent.length === 1 ? stale() : tag(question({ kind, version: 5, confirmed_text: "My wording" }), '"5"');
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/questions`);
  const card = await screen.findByRole("article", { name: "Question 1" });
  await user.click(within(card).getByRole("button", { name: "Edit wording" }));
  const box = within(card).getByLabelText("Your wording");
  await user.clear(box);
  await user.type(box, "My wording");
  await user.click(within(card).getByRole("button", { name: "Save the wording" }));
  const choice = await screen.findByRole("dialog");
  expect(within(choice).getByText("My wording")).toBeInTheDocument();
  expect(within(choice).getByText("Reworded by someone else")).toBeInTheDocument();
  await user.click(within(choice).getByRole("button", { name: KEEP_MINE }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]).toEqual({ body: { text: "My wording" }, ifMatch: '"4"' });
});
