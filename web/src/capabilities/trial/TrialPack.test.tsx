// S24 Trial pack: choosing what goes in (with the settings' version), making it from the served report,
// the "needs rebuilding" notice, downloading it behind the code, checking a new note against what was
// served, and the copy for the witness box (made, put on this device and removed, each behind the code).
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { memoryStore, openCopy, setCopyStore } from "./offline";
import { blobReply, CASE, COPY, copyOut, etagged, launchWords, packOut, sealed, servePack, settingsOut, term, type PackRun } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/trial-pack`;
const k = (key: string) => M[`trial.${key}`]!;
const fill = (text: string, params: Record<string, string | number>) => Object.entries(params).reduce((s, [p, v]) => s.replaceAll(`{{${p}}}`, String(v)), text);

beforeEach(() => {
  setCopyStore(memoryStore());
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:pack"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows what goes in, ticked from the settings, and the pack that is ready", async () => {
    servePack(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 1, name: k("pack.title") })).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: k("pack.section.report") })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: k("pack.section.chronology") })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: fill(k("pack.section.joint_statement"), { document: term(run, "expert_meeting_document") }) })).not.toBeChecked();
    expect(screen.getByText(fill(k("pack.ready"), { pages: 54 }))).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });
});

test("saving what goes in sends the sections with the settings' version", async () => {
  const { sent, record } = servePack("launch");
  server.use(http.put("/v1/cases/:caseId/trial-pack/settings", async ({ request }) => (await record(request), etagged(settingsOut({ sections: ["report", "chronology", "written_answers"], version: 3 }), 3))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("checkbox", { name: k("pack.section.written_answers") }));
  await user.click(screen.getByRole("button", { name: k("pack.save") }));
  await waitFor(() =>
    expect(sent.at(-1)).toMatchObject({
      method: "PUT", path: `/v1/cases/${CASE}/trial-pack/settings`, ifMatch: '"2"',
      body: { sections: ["report", "chronology", "written_answers"], include_guidance: false },
    }),
  );
  expect(await screen.findByText(k("pack.saved"))).toBeInTheDocument();
});

test("making the pack waits for the job, then shows the new pack", async () => {
  const { sent, record } = servePack("launch", { latest: { pack: null, settings: settingsOut() } });
  let done = false;
  server.use(
    http.post("/v1/cases/:caseId/trial-pack", async ({ request }) => (await record(request), json({ job_id: "a9999999-9999-4999-8999-999999999991" }, 202))),
    http.get("/v1/jobs/:jobId", () => {
      done = true;
      return json({ id: "a9999999-9999-4999-8999-999999999991", kind: "build_trial_pack", status: "done", pct: 100, step: null, eta_seconds: null, error: null });
    }),
    http.get("/v1/cases/:caseId/trial-pack/latest", () => json({ pack: done ? packOut({ page_count: 61 }) : null, settings: settingsOut() })),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.build") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/trial-pack` }));
  expect(await screen.findByText(fill(k("pack.ready"), { pages: 61 }), {}, { timeout: 4000 })).toBeInTheDocument();
});

test("a pack made before the court bundle changed says it needs rebuilding", async () => {
  servePack("launch", { latest: { pack: packOut({ stale: true, stale_reason: "court_bundle_superseded" }), settings: settingsOut() } });
  renderAt(url);
  expect(await screen.findByText(k("pack.stale"))).toBeInTheDocument();
});

test("a case not yet served cannot have a pack", async () => {
  servePack("launch", { latest: { pack: null, settings: settingsOut() } });
  server.use(http.post("/v1/cases/:caseId/trial-pack", () => apiError(409, "not_served")));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.build") }));
  expect(await screen.findByText(k("pack.error.not_served"))).toBeInTheDocument();
});

test("downloading the pack asks for the code when it is needed", async () => {
  servePack("launch");
  server.use(http.get("/v1/cases/:caseId/trial-pack/latest/download", () => apiError(403, "step_up_required")));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.download") }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

test("downloading the pack saves the PDF", async () => {
  const { sent, record } = servePack("launch");
  server.use(http.get("/v1/cases/:caseId/trial-pack/latest/download", async ({ request }) => (await record(request), blobReply("application/pdf"))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.download") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "GET", path: `/v1/cases/${CASE}/trial-pack/latest/download` }));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
});

test("checking a new note says which sentences come from what was served", async () => {
  const { sent, record } = servePack("launch");
  const reply: Schemas["NoteChecked"] = {
    sentences: [
      { index: 0, text: "The plan was recorded.", sourced: true, source: { kind: "report", para_no: 2 }, reason: "same_words" },
      { index: 1, text: "Zz the patient moved abroad.", sourced: false, source: null, reason: "not_found" },
    ],
  };
  server.use(http.post("/v1/cases/:caseId/trial-pack/check-new", async ({ request }) => (await record(request), json(reply))));
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(k("pack.check.label")), "The plan was recorded. Zz the patient moved abroad.");
  await user.click(screen.getByRole("button", { name: k("pack.check.run") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/trial-pack/check-new`, body: { text: "The plan was recorded. Zz the patient moved abroad." } }));
  expect(await screen.findByText(fill(k("pack.check.from_report"), { para: 2 }))).toBeInTheDocument();
  expect(screen.getByText(k("pack.check.not_served"))).toBeInTheDocument();
});

test("a copy for the witness box is made, put on this device sealed, and can be opened there", async () => {
  const { sent, record } = servePack("launch");
  let copies: Schemas["PackageOut"][] = [];
  const download = await sealed();
  server.use(
    http.get("/v1/cases/:caseId/offline-packages", () => json({ packages: copies })),
    http.post("/v1/cases/:caseId/offline-packages", async ({ request }) => {
      await record(request);
      copies = [copyOut()];
      return json(copies[0]!, 201);
    }),
    http.get("/v1/cases/:caseId/offline-packages/:packageId/download", async ({ request }) => {
      await record(request);
      copies = [copyOut({ status: "downloaded", downloaded_at: "2027-06-03T10:00:00Z" })];
      return json(download);
    }),
  );
  const { user } = renderAt(url);
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  await user.type(within(section).getByLabelText(k("pack.offline.device")), "My tablet");
  await user.click(within(section).getByRole("button", { name: k("pack.offline.create") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/offline-packages`, body: { device_label: "My tablet", include_images: false } }));
  await user.click(await within(section).findByRole("button", { name: k("pack.offline.download") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "GET", path: `/v1/cases/${CASE}/offline-packages/${COPY}/download` }));
  const link = await within(section).findByRole("link", { name: k("pack.offline.open") });
  expect(link).toHaveAttribute("href", `/cases/${CASE}/witness-box`);
  const opened = await openCopy(CASE);
  expect(opened.state).toBe("open");
});

test("removing the copy asks the server and forgets it on this device", async () => {
  const { sent, record } = servePack("launch", { copies: [copyOut({ status: "downloaded", downloaded_at: "2027-06-03T10:00:00Z" })] });
  server.use(http.delete("/v1/cases/:caseId/offline-packages/:packageId", async ({ request }) => (await record(request), new Response(null, { status: 204 }))));
  const { user } = renderAt(url);
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  await user.click(await within(section).findByRole("button", { name: k("pack.offline.delete") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "DELETE", path: `/v1/cases/${CASE}/offline-packages/${COPY}` }));
  expect(await within(section).findByText(k("pack.offline.deleted"))).toBeInTheDocument();
  expect((await openCopy(CASE)).state).toBe("none");
});
