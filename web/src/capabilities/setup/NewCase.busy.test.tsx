// S04: every action shows it is working and ignores a second click while it runs.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { caseItem, casePack, combination, inCase, jurisdiction } from "./testing";

function details() {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem("launch", { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
  );
  return renderAt(`/cases/${CASE_A}/details`);
}

test("saving the case details goes busy and sends once", async () => {
  const h = hold();
  const { user } = details();
  server.use(http.patch("/v1/cases/:caseId", async () => (await h.enter(), HttpResponse.json(caseItem("launch"), { headers: { etag: '"4"' } }))));
  try {
    await expectBusyOnce(user, await byName("Save case details"), h);
  } finally { h.release(); }
});

test("adding who instructed you goes busy and sends once", async () => {
  const h = hold();
  const { user } = details();
  server.use(http.post("/v1/cases/:caseId/instructing-parties", async () => (await h.enter(), json({}, 201))));
  try {
    const box = within(await screen.findByRole("region", { name: "Who instructed you?" }));
    await user.type(await box.findByLabelText("Their name"), "Ms Rees");
    await user.type(box.getByLabelText("Firm or organisation"), "Rees & Co");
    await expectBusyOnce(user, box.getByRole("button", { name: "Add" }), h);
  } finally { h.release(); }
});

test("checking for the same person goes busy and sends once", async () => {
  const h = hold();
  const { user } = details();
  server.use(
    http.post("/v1/cases/duplicate-check", async () => (await h.enter(), json({ matches: [] }))),
    http.put("/v1/cases/:caseId/subject-key", () => new HttpResponse(null, { status: 204 })),
  );
  try {
    const box = within(await screen.findByRole("region", { name: "Have you had a case about this person before?" }));
    await user.type(await box.findByLabelText("Their identifier number"), "9434765919");
    await expectBusyOnce(user, box.getByRole("button", { name: "Check" }), h);
  } finally { h.release(); }
});

test("saving what you were told goes busy and sends once", async () => {
  const h = hold();
  const { user } = details();
  server.use(http.put("/v1/cases/:caseId/oral-instructions", async () => (await h.enter(), json({ id: "f5555555-5555-4555-8555-555555555555", version_no: 2, received_on: "2026-10-03", source: "typed", text: "x" }))));
  try {
    const box = within(await screen.findByRole("region", { name: "Were you told anything by phone that isn't in the letter?" }));
    await user.type(await box.findByLabelText("What you were told"), "Concentrate on May.");
    await user.type(box.getByLabelText("When were you told?"), "2026-10-03");
    await expectBusyOnce(user, box.getByRole("button", { name: "Save what you were told" }), h);
  } finally { h.release(); }
});

test("changing who the case is for says the template is being checked and hides the old answer", async () => {
  const A = "7b1c0000-0000-4000-8000-000000000001";
  const B = "7b1c0000-0000-4000-8000-000000000002";
  const h = hold();
  const base = me();
  const team = (id: string, name: string) => ({ organisation_id: id, name, kind: "team" as const, role: "expert" as const, started_at: "2026-01-05T09:00:00Z", member_count: 3 });
  signedIn({ me: { ...base, memberships: [...base.memberships, team(A, "Carter Agency"), team(B, "Dunn Agency")] }, pack: casePack("launch") });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/new-case/template", async ({ request }) => {
      const org = new URL(request.url).searchParams.get("organisation_id");
      if (org === B) await h.enter();
      return json({ organisation_id: org, required_template_id: org === A ? "c0de0000-0000-4000-8000-000000000009" : null, locked: org === A });
    }),
  );
  const { user } = renderAt("/cases/new");
  try {
    await user.click(await screen.findByRole("radio", { name: "Carter Agency" }));
    expect(await screen.findByText(/Carter Agency's report template is used/)).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Dunn Agency" }));
    expect(await screen.findByText("Checking the report template…")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Carter Agency's report template is used/)).not.toBeInTheDocument());
  } finally { h.release(); }
});
