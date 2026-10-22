// S04 as one screen: drop the letter, the records and anything else, then a few questions in the pack's
// words, then "Prepare my bundle". The case is made the first time something needs it.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_B, me, TEAM_ORG } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { caseItem, casePack, combination, jurisdiction, launchWords, type PackRun } from "./testing";

function open(run: PackRun, teams = true) {
  const person = me();
  if (teams) person.memberships.push({ organisation_id: TEAM_ORG, name: "Northgate Medico-Legal", kind: "team", role: "member", started_at: "2026-02-01T09:00:00Z", member_count: 1 });
  signedIn({ me: person, pack: casePack(run) });
  server.use(
    http.get("/v1/packs", () => json([combination(run)])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction(run))),
  );
}

const statuses = (run: PackRun) => Object.values((jurisdiction(run).vocabulary.subject_statuses ?? {}) as Record<string, string>);
const cap = (w: string) => w.replace(/^./, (c) => c.toUpperCase());

/** Case creation, uploads, letter, prefill, parties, oral instructions, processing: each records what it was sent. */
function api(calls: string[], sent: Record<string, unknown> = {}) {
  server.use(
    http.post("/v1/cases", async ({ request }) => {
      sent.create = await request.json();
      calls.push("create");
      return HttpResponse.json(caseItem("launch", { id: CASE_B, organisation_id: TEAM_ORG }), { status: 201, headers: { etag: '"1"' } });
    }),
    http.patch("/v1/cases/:caseId", async ({ request }) => {
      sent.patch = await request.json();
      calls.push("patch");
      return HttpResponse.json(caseItem("launch", { id: CASE_B }), { headers: { etag: '"2"' } });
    }),
    http.post("/v1/cases/:caseId/uploads", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.push(`start:${String(body.kind)}`);
      return json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201);
    }),
    http.put("/v1/uploads/:id/chunks/:n", () => new HttpResponse(null, { status: 204 })),
    http.post("/v1/uploads/:id/complete", () => json({ document_id: "b7777777-7777-4777-8777-777777777777" })),
    http.post("/v1/cases/:caseId/instructions", async ({ request }) => {
      sent.instruction = await request.json();
      calls.push("instruction");
      return json({ id: "b1111111-1111-4111-8111-111111111111", kind: "letter", version_no: 1, document_id: "b7777777-7777-4777-8777-777777777777", job_id: "c8888888-8888-4888-8888-888888888888", locked: false, received_on: null, reopens_questions: false }, 201);
    }),
    http.post("/v1/cases/:caseId/prefill", () => {
      calls.push("prefill");
      return json({ job_id: "d9999999-9999-4999-8999-999999999999" }, 202);
    }),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: true, rejected: 0, suggestions: [] })),
    http.post("/v1/cases/:caseId/parties", async ({ request }) => {
      sent.party = await request.json();
      calls.push("party");
      return json({ id: "e1111111-1111-4111-8111-111111111111", role_key: "other_side", name: "Dr P", representative: null }, 201);
    }),
    http.post("/v1/cases/:caseId/instructing-parties", async ({ request }) => {
      sent.instructing = await request.json();
      calls.push("instructing");
      return json({ id: "e2222222-2222-4222-8222-222222222222" }, 201);
    }),
    http.put("/v1/cases/:caseId/oral-instructions", async ({ request }) => {
      sent.oral = await request.json();
      calls.push("oral");
      return json({ ok: true });
    }),
    http.post("/v1/cases/:caseId/process", () => {
      calls.push("process");
      return json({ job_id: "f1111111-1111-4111-8111-111111111111" }, 202);
    }),
    http.get("/v1/cases/:caseId/conflicts", () => new HttpResponse(null, { status: 500 })),
  );
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("one screen: three places to drop files, then the questions in the pack's words, and it passes axe", async () => {
    open(run);
    const { container } = renderAt("/cases/new");
    expect(await screen.findByRole("heading", { level: 1, name: "Start a new case" })).toBeInTheDocument();
    for (const title of ["Letter of instruction", "Medical records", "Anything else"]) expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: /Who is this case for\?/ })).toBeInTheDocument();
    const side = screen.getByRole("radiogroup", { name: "Which side are you instructed by?" });
    const words = jurisdiction(run).vocabulary.party_roles as Record<string, string>;
    expect(within(side).getByRole("radio", { name: cap(words.other_side!) })).toBeInTheDocument();
    expect(screen.getByLabelText(String(jurisdiction(run).vocabulary.other_side_prompt))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: String(jurisdiction(run).vocabulary.other_side_add_another) })).toBeInTheDocument();
    const subject = screen.getByRole("radiogroup", { name: "The patient is" });
    for (const label of statuses(run)) expect(within(subject).getByRole("radio", { name: cap(label) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare my bundle" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save and finish later" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "/");
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("with one organisation the case is for your own practice without asking", async () => {
  open("launch", false);
  renderAt("/cases/new");
  await screen.findByRole("heading", { level: 1, name: "Start a new case" });
  expect(screen.queryByRole("radiogroup", { name: /Who is this case for\?/ })).toBeNull();
});

test("dropping the letter makes the case, uploads it, records it as the letter and starts reading it", async () => {
  open("launch");
  const calls: string[] = [];
  const sent: Record<string, unknown> = {};
  api(calls, sent);
  const { user } = renderAt("/cases/new");
  await user.click(await screen.findByRole("radio", { name: "Northgate Medico-Legal" }));
  await user.type(screen.getByLabelText("A short name for the case"), "Mrs B");
  const zone = screen.getByText("Letter of instruction").closest(".av-dropzone") as HTMLElement;
  await user.upload(zone.querySelector("input[type=file]") as HTMLInputElement, new File([new Uint8Array(1000)], "letter.pdf", { type: "application/pdf" }));
  await waitFor(() => expect(calls).toEqual(["create", "start:instructions", "instruction", "prefill"]));
  expect(sent.create).toMatchObject({ organisation_id: TEAM_ORG, label: "Mrs B" });
  expect(sent.instruction).toEqual({ kind: "letter", document_id: "b7777777-7777-4777-8777-777777777777" });
  expect(await screen.findByText("We're reading the letter. This takes a minute or two.")).toBeInTheDocument();
  expect(await within(zone).findByText("letter.pdf")).toBeInTheDocument();
});

test("medical records are uploaded as records and counted", async () => {
  open("launch");
  const calls: string[] = [];
  api(calls);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  const zone = screen.getByText("Medical records").closest(".av-dropzone") as HTMLElement;
  await user.upload(zone.querySelector("input[type=file]") as HTMLInputElement, [
    new File([new Uint8Array(10)], "gp.pdf", { type: "application/pdf" }),
    new File([new Uint8Array(10)], "hospital.pdf", { type: "application/pdf" }),
  ]);
  await waitFor(() => expect(calls.filter((c) => c === "start:records")).toHaveLength(2));
  expect(await within(zone).findByText("2 files")).toBeInTheDocument();
});

test("Prepare my bundle sends every answer, starts the bundle and opens the check before you start", async () => {
  open("launch");
  const calls: string[] = [];
  const sent: Record<string, unknown> = {};
  api(calls, sent);
  const { user, router } = renderAt("/cases/new");
  await user.click(await screen.findByRole("radio", { name: "Northgate Medico-Legal" }));
  await user.type(screen.getByLabelText("A short name for the case"), "Mrs B");
  await user.type(screen.getByLabelText("Who instructed you?"), "Ms Rees");
  await user.type(screen.getByLabelText("Their firm or organisation"), "Rees & Co");
  await user.click(within(screen.getByRole("radiogroup", { name: "Which side are you instructed by?" })).getAllByRole("radio")[1]!);
  await user.type(screen.getByLabelText(String(jurisdiction("launch").vocabulary.other_side_prompt)), "Dr P");
  await user.click(within(screen.getByRole("radiogroup", { name: "The patient is" })).getByRole("radio", { name: "Has died" }));
  await user.type(screen.getByLabelText("When is your report due?"), "2026-12-01");
  await user.type(screen.getByLabelText("Were you told anything by phone that isn't in the letter?"), "Look at the May visit.");
  await user.click(screen.getByRole("button", { name: "Prepare my bundle" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_B}/conflicts`));
  expect(calls).toEqual(["create", "instructing", "party", "oral", "process"]);
  expect(sent.create).toMatchObject({ organisation_id: TEAM_ORG, label: "Mrs B", party_role: "other_side", due_date: "2026-12-01", subject_status: "deceased" });
  expect(sent.party).toEqual({ role_key: "other_side", name: "Dr P" });
  expect(sent.instructing).toMatchObject({ name: "Ms Rees", firm: "Rees & Co" });
  expect(sent.oral).toMatchObject({ text: "Look at the May visit." });
});

test("Save and finish later keeps the case and goes back without starting the bundle", async () => {
  open("launch", false);
  const calls: string[] = [];
  api(calls);
  const { user, router } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  await user.click(screen.getByRole("button", { name: "Save and finish later" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/today"));
  expect(calls).toEqual(["create"]);
});

test("a file drop turns into an Added card, and the other two stay as they were", async () => {
  open("launch", false);
  const calls: string[] = [];
  api(calls);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  const zone = screen.getByText("Letter of instruction").closest(".av-dropzone") as HTMLElement;
  const card = zone;
  expect(card).not.toHaveClass("av-dropzone--added");
  await user.upload(zone.querySelector("input[type=file]") as HTMLInputElement, new File([new Uint8Array(1000)], "letter.pdf", { type: "application/pdf" }));
  await waitFor(() => expect(card).toHaveClass("av-dropzone--added"));
  expect(within(card).getByText("Added")).toBeInTheDocument();
  expect(screen.getByText("Medical records").closest(".av-dropzone") as HTMLElement).not.toHaveClass("av-dropzone--added");
});

test("the patient and the court track are small switches, the side is a row of pills", async () => {
  open("launch");
  renderAt("/cases/new");
  const subject = await screen.findByRole("radiogroup", { name: "The patient is" });
  expect(subject).toHaveClass("av-segmented");
  expect(screen.getByRole("radiogroup", { name: "Which court track?" })).toHaveClass("av-segmented");
  expect(screen.getByRole("radiogroup", { name: "Which side are you instructed by?" })).toHaveClass("av-pill-group");
});

test("a joint instruction adds another instructing party, and each one is sent", async () => {
  open("launch", false);
  const calls: string[] = [];
  const sent: Record<string, unknown> = {};
  const bodies: unknown[] = [];
  api(calls, sent);
  server.use(
    http.post("/v1/cases/:caseId/instructing-parties", async ({ request }) => {
      bodies.push(await request.json());
      calls.push("instructing");
      return json({ id: `e${bodies.length}222222-2222-4222-8222-222222222222` }, 201);
    }),
  );
  const { user, router } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  await user.type(screen.getByLabelText("Who instructed you?"), "Ms Rees");
  await user.type(screen.getByLabelText("Their firm or organisation"), "Rees & Co");
  await user.click(screen.getByRole("button", { name: "Add another (if you're instructed jointly)" }));
  await user.type(screen.getByLabelText("Who instructed you? (2)"), "Mr Hale");
  await user.type(screen.getByLabelText("Their firm or organisation (2)"), "Hale LLP");
  await user.click(screen.getByRole("button", { name: "Prepare my bundle" }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_B}/conflicts`));
  expect(bodies).toEqual([expect.objectContaining({ name: "Ms Rees", firm: "Rees & Co" }), expect.objectContaining({ name: "Mr Hale", firm: "Hale LLP" })]);
});

test("what you were told by phone sits in a framed box that offers Dictate once the case exists, and Prepare has an arrow", async () => {
  open("launch", false);
  const calls: string[] = [];
  api(calls);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  expect(screen.getByLabelText("Were you told anything by phone that isn't in the letter?")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Dictate" })).toBeNull();
  const zone = screen.getByText("Medical records").closest(".av-dropzone") as HTMLElement;
  await user.upload(zone.querySelector("input[type=file]") as HTMLInputElement, new File([new Uint8Array(10)], "gp.pdf", { type: "application/pdf" }));
  expect(await screen.findByRole("button", { name: "Dictate" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare my bundle" }).querySelector("svg")).not.toBeNull();
});
