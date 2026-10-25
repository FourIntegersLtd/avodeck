// Add more papers: pick files, say what they are and how and when they arrived, upload them as one
// new delivery, follow the processing, and go back to the case home. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, JOB, processingJob, summary, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/papers/add`;
const DOC_A = "d3000000-0000-4000-8000-000000000001";
const DOC_B = "d3000000-0000-4000-8000-000000000002";
const pdf = (name: string) => new File([new Uint8Array(300)], name, { type: "application/pdf" });

function mock(pack: Schemas["CasePack"], opts: { actions?: string[]; failUpload?: boolean; jobs?: Array<Partial<Schemas["ProcessingJob"]>> } = {}) {
  signedIn({ pack });
  const seen = { starts: [] as Array<Record<string, unknown>>, chunks: 0, patches: [] as Array<{ id: string; body: Record<string, unknown> }>, appends: [] as unknown[] };
  const ids = [DOC_A, DOC_B];
  const jobs = [...(opts.jobs ?? [{ status: "done", step: "missing", pct: 100 }])];
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase(opts.actions ? { allowed_actions: opts.actions } : {}))),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack, { documents: [] }))),
    http.get(`/v1/cases/${CASE_ID}/progress`, () => json({ read: 0, total: 0, seconds_left: 0 })),
    http.get(`/v1/cases/${CASE_ID}/steps/progress`, () => json({ steps: [] })),
    http.get(`/v1/cases/${CASE_ID}/questions`, () => json({ questions: [], assumptions: [], locked: false, instruction_version_id: null })),
    http.post(`/v1/cases/${CASE_ID}/uploads`, async ({ request }) => {
      if (opts.failUpload) return json({ code: "internal", message: "no" }, 500);
      seen.starts.push((await request.json()) as Record<string, unknown>);
      return json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201);
    }),
    http.put("/v1/uploads/:id/chunks/:n", () => {
      seen.chunks += 1;
      return new Response(null, { status: 204 });
    }),
    http.post("/v1/uploads/:id/complete", () => json({ document_id: ids[seen.chunks - 1] })),
    http.patch(`/v1/cases/${CASE_ID}/documents/:docId`, async ({ params, request }) => {
      seen.patches.push({ id: String(params.docId), body: (await request.json()) as Record<string, unknown> });
      return json({ id: params.docId });
    }),
    http.post(`/v1/cases/${CASE_ID}/documents/append`, async ({ request }) => {
      seen.appends.push(await request.json());
      return json({ job_id: JOB, bundle_version: 2 }, 202);
    }),
    http.get(`/v1/jobs/${JOB}`, () => json(processingJob(jobs.length > 1 ? jobs.shift()! : jobs[0]!))),
  );
  return seen;
}

describe.each([
  ["launchPack", launchPack],
  ["fixturePack", fixturePack],
])("Add more papers (%s)", (_name, pack) => {
  test("shows the choices, keeps the one main action off until files are chosen, and has no axe violations", async () => {
    mock(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { name: "Add more papers" })).toBeVisible();
    expect(screen.getByRole("radiogroup", { name: "What are they?" })).toBeVisible();
    expect(screen.getByRole("radiogroup", { name: "How did they reach you?" })).toBeVisible();
    expect(screen.getByLabelText("Date they arrived")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add these papers" })).toBeDisabled();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("uploads every file as later records of the chosen kind, records how and when they arrived, then follows the job", async () => {
    const seen = mock(pack, { jobs: [{ status: "running", step: "ocr", pct: 40 }, { status: "done", step: "missing", pct: 100 }] });
    const { user } = renderAt(PATH);
    await user.upload(await screen.findByLabelText("Choose files"), [pdf("a.pdf"), pdf("b.pdf")]);
    expect(screen.getByText("a.pdf")).toBeVisible();
    expect(screen.getByText("b.pdf")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: "Other papers" }));
    await user.click(screen.getByRole("radio", { name: "Sent by the instructing party" }));
    await user.click(screen.getByRole("button", { name: "Add these papers" }));

    await waitFor(() => expect(seen.appends).toEqual([{ document_ids: [DOC_A, DOC_B] }]));
    expect(seen.starts.map((s) => [s.filename, s.kind, s.append])).toEqual([["a.pdf", "other", true], ["b.pdf", "other", true]]);
    expect(seen.patches.map((p) => p.id)).toEqual([DOC_A, DOC_B]);
    expect(seen.patches[0]!.body).toMatchObject({ obtained_how: "Sent by the instructing party" });
    expect(String(seen.patches[0]!.body.received_on)).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    const done = await screen.findByText("Your new papers are ready.", {}, { timeout: 6000 });
    expect(done).toBeVisible();
    const back = screen.getByRole("link", { name: "Back to your papers" });
    expect(back).toHaveAttribute("href", `/cases/${CASE_ID}/home`);
    expect(within(back).queryByText("Add these papers")).toBeNull();
  }, 15000);

  test("a failed upload says so in plain words and keeps the files so the expert can try again", async () => {
    mock(pack, { failUpload: true });
    const { user } = renderAt(PATH);
    await user.upload(await screen.findByLabelText("Choose files"), pdf("a.pdf"));
    await user.click(screen.getByRole("button", { name: "Add these papers" }));
    expect(await screen.findByText(/couldn't be saved/)).toBeVisible();
    expect(screen.getByText("a.pdf")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add these papers" })).toBeEnabled();
  });

  test("someone who may not upload is told so and sees no upload control", async () => {
    mock(pack, { actions: VIEWER_ACTIONS });
    renderAt(PATH);
    expect(await screen.findByText("Only the person who was instructed can add papers to this case.")).toBeVisible();
    expect(screen.queryByLabelText("Choose files")).toBeNull();
  });
});
