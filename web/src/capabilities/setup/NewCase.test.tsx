// S04 New case: who the case is for, the pack, the side; then the letter, what was found in it (suggestions
// the expert accepts one by one, never applied by themselves), who instructed them (jointly or not), the
// parties, a duplicate warning and oral instructions.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_A, CASE_B, me, PERSONAL_ORG, TEAM_ORG } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { caseItem, casePack, combination, inCase, jurisdiction, launchWords, type PackRun } from "./testing";

const IV = "b1111111-1111-4111-8111-111111111111";

function twoOrganisations(run: PackRun) {
  const person = me();
  person.memberships.push({ organisation_id: TEAM_ORG, name: "Northgate Medico-Legal", kind: "team", role: "member", started_at: "2026-02-01T09:00:00Z" });
  signedIn({ me: person, pack: casePack(run) });
  server.use(
    http.get("/v1/packs", () => json([combination(run)])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction(run))),
  );
}

function details(run: PackRun, opts: { suggestions?: Array<Partial<{ id: string; field: string; value: string }>> } = {}) {
  inCase(run);
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem(run, { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () =>
      json({
        reading: false,
        rejected: 0,
        suggestions: (opts.suggestions ?? []).map((s, i) => ({
          id: s.id ?? `d000000${i}-1111-4111-8111-111111111111`,
          field: s.field ?? "due_date",
          value: s.value ?? "2026-11-30",
          status: "suggested",
          word_ids: ["p1-w4"],
          decided_at: null,
          instruction_version_id: IV,
        })),
      }),
    ),
  );
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the new case form asks who it is for and which side, in the pack's words, and passes axe", async () => {
    twoOrganisations(run);
    const { container } = renderAt("/cases/new");
    expect(await screen.findByRole("heading", { level: 1, name: "Start a new case" })).toBeInTheDocument();
    const who = await screen.findByRole("radiogroup", { name: /Who is this case for\?/ });
    expect(within(who).getByRole("radio", { name: "My own practice" })).toBeInTheDocument();
    expect(within(who).getByRole("radio", { name: "Northgate Medico-Legal" })).toBeInTheDocument();
    const side = await screen.findByRole("radiogroup", { name: "Which side are you instructed by?" });
    const words = jurisdiction(run).vocabulary.party_roles as Record<string, string>;
    expect(within(side).getByRole("radio", { name: words.other_side!.replace(/^./, (c) => c.toUpperCase()) })).toBeInTheDocument();
    if (jurisdiction(run).court_tracks.length) {
      expect(screen.getByRole("radiogroup", { name: "Which court track?" })).toBeInTheDocument();
    } else {
      expect(screen.queryByRole("radiogroup", { name: "Which court track?" })).toBeNull();
    }
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the case details page shows who instructed you in the pack's words, and passes axe", async () => {
    details(run, { suggestions: [{ field: "party_role", value: "other_side" }] });
    const { container } = renderAt(`/cases/${CASE_A}/details`);
    expect(await screen.findByRole("heading", { level: 2, name: "Set up the case" })).toBeInTheDocument();
    const found = await screen.findByRole("region", { name: "Found in the letter" });
    const words = casePack(run).party_roles.find((r) => r.key === "other_side")!.label!;
    expect(await within(found).findByText(new RegExp(words, "i"))).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Who instructed you?" })).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("starting a case sends who it is for, the pack and the side, then opens its details", async () => {
  twoOrganisations("launch");
  let sent: Record<string, unknown> | undefined;
  server.use(
    http.post("/v1/cases", async ({ request }) => {
      sent = (await request.json()) as Record<string, unknown>;
      return json(caseItem("launch", { id: CASE_B, organisation_id: TEAM_ORG }), 201);
    }),
  );
  const { user, router } = renderAt("/cases/new");
  await user.click(await screen.findByRole("radio", { name: "Northgate Medico-Legal" }));
  await user.type(screen.getByLabelText("A short name for the case"), "Mrs B");
  const side = screen.getByRole("radiogroup", { name: "Which side are you instructed by?" });
  await user.click(within(side).getAllByRole("radio")[1]!);
  await user.type(screen.getByLabelText("When is your report due?"), "2026-12-01");
  await user.click(screen.getByRole("button", { name: "Start the case" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_B}/details`));
  expect(sent).toMatchObject({
    organisation_id: TEAM_ORG,
    jurisdiction: "gb-eaw",
    case_type: "clin-neg-breach",
    specialty: "general-practice",
    label: "Mrs B",
    party_role: "other_side",
    due_date: "2026-12-01",
  });
});

test("with one organisation the case is for your own practice without asking", async () => {
  signedIn({ me: me() });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
  );
  let sent: Record<string, unknown> | undefined;
  server.use(
    http.post("/v1/cases", async ({ request }) => {
      sent = (await request.json()) as Record<string, unknown>;
      return json(caseItem("launch"), 201);
    }),
  );
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs A");
  expect(screen.queryByRole("radiogroup", { name: /Who is this case for\?/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Start the case" }));
  await waitFor(() => expect(sent).toMatchObject({ organisation_id: PERSONAL_ORG, label: "Mrs A" }));
});

test("a suggestion from the letter is applied only when you accept it, and saved only when you save", async () => {
  details("launch", { suggestions: [{ id: "d1000000-1111-4111-8111-111111111111", field: "due_date", value: "2026-11-30" }] });
  let decision: unknown;
  let saved: { body: unknown; ifMatch: string | null } | undefined;
  server.use(
    http.patch("/v1/cases/:caseId/prefill/:id", async ({ request, params }) => {
      decision = { id: params.id, ...((await request.json()) as object) };
      return json({ id: params.id, field: "due_date", value: "2026-11-30", status: "accepted", word_ids: [], decided_at: "2026-10-17T09:00:00Z", instruction_version_id: IV });
    }),
    http.patch("/v1/cases/:caseId", async ({ request }) => {
      saved = { body: await request.json(), ifMatch: request.headers.get("if-match") };
      return HttpResponse.json(caseItem("launch"), { headers: { etag: '"4"' } });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const due = await screen.findByLabelText("When is your report due?");
  expect(due).toHaveValue("");
  const found = await screen.findByRole("region", { name: "Found in the letter" });
  await user.click(within(found).getByRole("button", { name: "Use this" }));
  await waitFor(() => expect(decision).toEqual({ id: "d1000000-1111-4111-8111-111111111111", decision: "accepted" }));
  expect(due).toHaveValue("2026-11-30");
  expect(saved).toBeUndefined();
  await user.click(screen.getByRole("button", { name: "Save case details" }));
  await waitFor(() => expect(saved).toBeDefined());
  expect(saved!.ifMatch).toBe('"3"');
  expect(saved!.body).toMatchObject({ label: "Mrs A", due_date: "2026-11-30" });
});

test("dismissing a suggestion records it and changes nothing", async () => {
  details("launch", { suggestions: [{ id: "d2000000-1111-4111-8111-111111111111", field: "due_date", value: "2026-11-30" }] });
  let decision: unknown;
  server.use(
    http.patch("/v1/cases/:caseId/prefill/:id", async ({ request }) => {
      decision = await request.json();
      return json({ id: "d2000000-1111-4111-8111-111111111111", field: "due_date", value: "2026-11-30", status: "dismissed", word_ids: [], decided_at: null, instruction_version_id: IV });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const found = await screen.findByRole("region", { name: "Found in the letter" });
  await user.click(await within(found).findByRole("button", { name: "Not this" }));
  await waitFor(() => expect(decision).toEqual({ decision: "dismissed" }));
  expect(screen.getByLabelText("When is your report due?")).toHaveValue("");
});

test("adding who instructed you sends their type in the pack's key, and a second one makes it joint", async () => {
  details("launch");
  const sent: unknown[] = [];
  server.use(
    http.post("/v1/cases/:caseId/instructing-parties", async ({ request }) => {
      const body = (await request.json()) as Record<string, string>;
      sent.push(body);
      const party = { id: `e${sent.length}111111-1111-4111-8111-111111111111`, effective_from: "2026-10-17", effective_to: null, email: null, reference: null, replaces_id: null, ...body };
      server.use(
        http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: sent.map((b, i) => ({ ...party, ...(b as object), id: `e${i + 1}111111-1111-4111-8111-111111111111` })), history: [], joint: sent.length > 1 })),
      );
      return json(party, 201);
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const who = await screen.findByRole("region", { name: "Who instructed you?" });
  await user.type(within(who).getByLabelText("Their name"), "Ms Rees");
  await user.type(within(who).getByLabelText("Firm or organisation"), "Rees & Co");
  await user.click(within(who).getByRole("button", { name: "Add" }));
  await waitFor(() => expect(sent).toEqual([{ type_key: "lawyer", name: "Ms Rees", firm: "Rees & Co" }]));
  expect(await within(who).findByText("Ms Rees, Rees & Co")).toBeInTheDocument();
  await user.click(within(who).getByRole("button", { name: "Add another (if you're instructed jointly)" }));
  await user.type(within(who).getByLabelText("Their name"), "Mr Hale");
  await user.type(within(who).getByLabelText("Firm or organisation"), "Hale LLP");
  await user.click(within(who).getByRole("button", { name: "Add" }));
  expect(await within(who).findByText("You're instructed jointly. Notes go to everyone who instructed you.")).toBeInTheDocument();
});

test("a matching identifier warns that this may be a case you already have", async () => {
  details("launch");
  let checked: unknown;
  server.use(
    http.post("/v1/cases/duplicate-check", async ({ request }) => {
      checked = await request.json();
      return json({ matches: [{ case_id: CASE_B, label: "Mrs A (2025)", same_instructing_party: true }] });
    }),
    http.put("/v1/cases/:caseId/subject-key", () => new HttpResponse(null, { status: 204 })),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const same = await screen.findByRole("region", { name: "Have you had a case about this person before?" });
  await user.type(within(same).getByLabelText("Their identifier number"), "9434765919");
  await user.click(within(same).getByRole("button", { name: "Check" }));
  await waitFor(() => expect(checked).toEqual({ identifier: "9434765919", jurisdiction: "gb-eaw" }));
  expect(await within(same).findByText("You may already have a case about this person: Mrs A (2025).")).toBeInTheDocument();
});

test("oral instructions are typed or dictated and saved with the date", async () => {
  details("launch");
  let sent: unknown;
  server.use(
    http.put("/v1/cases/:caseId/oral-instructions", async ({ request }) => {
      sent = await request.json();
      return json({ id: "f5555555-5555-4555-8555-555555555555", version_no: 2, received_on: "2026-10-03", source: "typed", text: "Concentrate on May." });
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const oral = await screen.findByRole("region", { name: "Were you told anything by phone that isn't in the letter?" });
  await user.type(within(oral).getByLabelText("What you were told"), "Concentrate on May.");
  await user.type(within(oral).getByLabelText("When were you told?"), "2026-10-03");
  await user.click(within(oral).getByRole("button", { name: "Save what you were told" }));
  await waitFor(() => expect(sent).toEqual({ received_on: "2026-10-03", text: "Concentrate on May." }));
  expect(await within(oral).findByText("Saved")).toBeInTheDocument();
});

test("adding the letter uploads it, records it as the letter and starts reading it", async () => {
  details("launch");
  const calls: string[] = [];
  let instruction: unknown;
  server.use(
    http.post("/v1/cases/:caseId/uploads", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.push(`start:${String(body.kind)}:${String(body.size)}`);
      return json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201);
    }),
    http.put("/v1/uploads/:id/chunks/:n", ({ params }) => {
      calls.push(`chunk:${String(params.n)}`);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("/v1/uploads/:id/complete", () => {
      calls.push("complete");
      return json({ document_id: "b7777777-7777-4777-8777-777777777777" });
    }),
    http.post("/v1/cases/:caseId/instructions", async ({ request }) => {
      instruction = await request.json();
      return json({ id: IV, kind: "letter", version_no: 1, document_id: "b7777777-7777-4777-8777-777777777777", job_id: "c8888888-8888-4888-8888-888888888888", locked: false, received_on: null, reopens_questions: false }, 201);
    }),
    http.post("/v1/cases/:caseId/prefill", () => {
      calls.push("prefill");
      return json({ job_id: "d9999999-9999-4999-8999-999999999999" }, 202);
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const letter = await screen.findByRole("region", { name: "Letter of instruction" });
  await user.upload(within(letter).getByLabelText("Add the letter of instruction"), new File([new Uint8Array(1000)], "letter.pdf", { type: "application/pdf" }));
  await waitFor(() => expect(calls).toEqual(["start:instructions:1000", "chunk:1", "complete", "prefill"]));
  expect(instruction).toEqual({ kind: "letter", document_id: "b7777777-7777-4777-8777-777777777777" });
  expect(await within(letter).findByText("We're reading the letter. This takes a minute or two.")).toBeInTheDocument();
});
