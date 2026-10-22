// S24 Trial pack: choosing what goes in (with the settings' version), making it from the served report,
// the "needs rebuilding" notice, downloading it behind the code, checking a new note against what was
// served, and the copy for the witness box (made, put on this device and removed, each behind the code).
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { findCopy, memoryStore, setCopyStore, unlockCopy } from "./offline";
import { blobReply, CASE, COPY, copyOut, etagged, launchWords, packOut, PIN, previewOut, sealed, servePack, settingsOut, term, type PackRun } from "./testing";
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
    expect(await screen.findByRole("heading", { level: 2, name: k("pack.title") })).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: k("pack.section.report") })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: k("pack.section.chronology") })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: fill(k("pack.section.joint_and_answers"), { document: term(run, "expert_meeting_document") }) })).not.toBeChecked();
    expect(screen.getByText(fill(k("pack.summary"), { pages: 54 }))).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });
});

test("saving what goes in sends the sections with the settings' version", async () => {
  const { sent, record } = servePack("launch");
  server.use(http.put("/v1/cases/:caseId/trial-pack/settings", async ({ request }) => (await record(request), etagged(settingsOut({ sections: ["report", "chronology", "written_answers"], version: 3 }), 3))));
  const { user } = renderAt(url);
  // The board draws the joint statement and the written answers as one row; ticking it sets both.
  const box = await screen.findByRole("checkbox", { name: fill(k("pack.section.joint_and_answers"), { document: term("launch", "expert_meeting_document") }) });
  expect(screen.queryByRole("button", { name: k("pack.save") })).not.toBeInTheDocument();
  await user.click(box);
  await user.click(screen.getByRole("button", { name: k("pack.save") }));
  await waitFor(() =>
    expect(sent.at(-1)).toMatchObject({
      method: "PUT", path: `/v1/cases/${CASE}/trial-pack/settings`, ifMatch: '"2"',
      body: { sections: ["report", "chronology", "joint_statement", "written_answers"], include_guidance: false },
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
  expect(await screen.findByText(fill(k("pack.summary"), { pages: 61 }), {}, { timeout: 4000 })).toBeInTheDocument();
});

test("a pack made before the court bundle changed says it needs rebuilding", async () => {
  servePack("launch", { latest: { pack: packOut({ stale: true, stale_reason: "court_bundle_superseded" }), settings: settingsOut() } });
  renderAt(url);
  expect(await screen.findByText(k("pack.stale"))).toBeInTheDocument();
});

test.each(["hearing_adjourned", "hearing_changed", "new_pages", "new_served_version"])("a pack made before %s says why it needs rebuilding", async (reason) => {
  servePack("launch", { latest: { pack: packOut({ stale: true, stale_reason: reason }), settings: settingsOut() } });
  renderAt(url);
  expect(await screen.findByText(k("pack.stale"))).toBeInTheDocument();
  expect(screen.getByText(k(`pack.stale_reason.${reason}`))).toBeInTheDocument();
});

test("a copy for the witness box made before something changed says to make it again, and why", async () => {
  servePack("launch", { copies: [copyOut({ status: "downloaded", downloaded_at: "2027-06-03T10:00:00Z", stale: true, stale_reason: "hearing_adjourned" })] });
  const { container, user } = renderAt(url);
  // The board keeps the witness-box copy behind one link.
  await user.click(await screen.findByRole("button", { name: k("pack.offline.show") }));
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  expect(await within(section).findByText(k("pack.offline.stale"))).toBeInTheDocument();
  expect(within(section).getByText(k("pack.stale_reason.hearing_adjourned"))).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("a current copy shows no warning", async () => {
  servePack("launch", { copies: [copyOut()] });
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.offline.show") }));
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  await within(section).findByText(fill(k("pack.offline.copy_line"), { device: "My tablet", date: "1 July 2099" }));
  expect(within(section).queryByText(k("pack.offline.stale"))).not.toBeInTheDocument();
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
  await user.click(await screen.findByRole("button", { name: k("pack.check.open") }));
  await user.type(await screen.findByLabelText(k("pack.check.label")), "The plan was recorded. Zz the patient moved abroad.");
  await user.click(screen.getByRole("button", { name: k("pack.check.run") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/trial-pack/check-new`, body: { text: "The plan was recorded. Zz the patient moved abroad." } }));
  expect(await screen.findByText(fill(k("pack.check.from_report"), { para: 2 }))).toBeInTheDocument();
  // A sentence found in nothing served is the amber "nothing new on the day" notice, quoting it.
  const notice = await screen.findByRole("region", { name: k("pack.check.heading") });
  expect(within(notice).getByText(k("pack.check.heading"))).toBeInTheDocument();
  expect(within(notice).getByText(/Zz the patient moved abroad\./)).toBeInTheDocument();
});

test("the pack says when it was built", async () => {
  servePack("launch");
  renderAt(url);
  const built = fill(k("pack.built"), { date: "3 June 2027" });
  expect(await screen.findByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").includes(built))).toBeInTheDocument();
  // The date is when the pack was made, not a hearing date.
  expect(screen.getByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").includes("Made on 3 June 2027."))).toBeInTheDocument();
  expect(screen.queryByText(/Built for the trial/)).not.toBeInTheDocument();
});

test("a reply is written under a question's sheet and saved with the question number", async () => {
  const { sent, record } = servePack("launch");
  server.use(http.put("/v1/cases/:caseId/readiness-replies/:n", async ({ request }) => (await record(request), json({ question_no: 1, reply: "Zz my reply.", replied_at: "2027-06-04T10:00:00Z" }))));
  const { user } = renderAt(url);
  const preview = await screen.findByRole("region", { name: k("pack.preview.heading") });
  await user.click(within(preview).getByRole("button", { name: k("pack.sheet.reply_write") }));
  await user.type(within(preview).getByLabelText(k("pack.sheet.my_reply")), "Zz my reply.");
  await user.click(within(preview).getByRole("button", { name: k("pack.sheet.reply_save") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PUT", path: `/v1/cases/${CASE}/readiness-replies/1`, body: { reply: "Zz my reply." } }));
  expect(await within(preview).findByText(fill(k("pack.sheet.replied"), { date: "4 June 2027" }))).toBeInTheDocument();
});

test("a reply already written is shown in the sheet with its date", async () => {
  const base = previewOut();
  servePack("launch", { preview: { ...base, sheets: [{ ...base.sheets[0]!, reply: "Zz kept reply.", replied_at: "2027-06-01T08:00:00Z" }, ...base.sheets.slice(1)] } });
  const { user } = renderAt(url);
  const preview = await screen.findByRole("region", { name: k("pack.preview.heading") });
  // The reply reads in the sheet's table; it is edited behind "Change my reply".
  expect(within(preview).getByText("Zz kept reply.")).toBeInTheDocument();
  expect(within(preview).getByText(fill(k("pack.sheet.replied"), { date: "1 June 2027" }))).toBeInTheDocument();
  expect(within(preview).queryByLabelText(k("pack.sheet.my_reply"))).not.toBeInTheDocument();
  await user.click(within(preview).getByRole("button", { name: k("pack.sheet.reply_edit") }));
  expect(within(preview).getByLabelText(k("pack.sheet.my_reply"))).toHaveValue("Zz kept reply.");
});

test("a sentence found in nothing served can be told to the instructing party or left out, and the choice is kept", async () => {
  const { sent, record } = servePack("launch");
  const NOTE_ID = "a9777777-7777-4777-8777-777777777777";
  const reply: Schemas["NoteChecked"] = {
    sentences: [
      { index: 0, text: "The plan was recorded.", sourced: true, source: { kind: "report", para_no: 2 }, reason: "same_words" },
      { index: 1, text: "Zz the patient moved abroad.", sourced: false, source: null, reason: "not_found", note_id: NOTE_ID, choice: null },
      { index: 2, text: "Zz and left.", sourced: false, source: null, reason: "not_found", note_id: "a9777777-7777-4777-8777-777777777778", choice: "leave_out" },
    ],
  };
  server.use(
    http.post("/v1/cases/:caseId/trial-pack/check-new", () => json(reply)),
    http.put("/v1/cases/:caseId/trial-pack/notes/:id", async ({ request }) => (await record(request), json({ id: NOTE_ID, text: "Zz the patient moved abroad.", choice: "tell", decided_at: "2027-06-04T10:00:00Z" }))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.check.open") }));
  await user.type(await screen.findByLabelText(k("pack.check.label")), "Zz the patient moved abroad.");
  await user.click(screen.getByRole("button", { name: k("pack.check.run") }));
  expect(await screen.findByText(k("pack.check.left_out"))).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: k("pack.check.leave_out") })).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: /^Tell / }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PUT", path: `/v1/cases/${CASE}/trial-pack/notes/${NOTE_ID}`, body: { choice: "tell" } }));
  expect(await screen.findByText(k("pack.check.told"))).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: k("pack.check.leave_out") })).not.toBeInTheDocument();
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
  await user.click(await screen.findByRole("button", { name: k("pack.offline.show") }));
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  await user.type(within(section).getByLabelText(k("pack.offline.device")), "My tablet");
  await user.click(within(section).getByRole("button", { name: k("pack.offline.create") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/offline-packages`, body: { device_label: "My tablet", include_images: false } }));
  const put = await within(section).findByRole("button", { name: k("pack.offline.download") });
  expect(put).toBeDisabled();
  await user.type(within(section).getByLabelText(k("pack.offline.pin")), "1234");
  await user.type(within(section).getByLabelText(k("pack.offline.pin_again")), "1234");
  expect(put).toBeDisabled();
  await user.clear(within(section).getByLabelText(k("pack.offline.pin")));
  await user.clear(within(section).getByLabelText(k("pack.offline.pin_again")));
  await user.type(within(section).getByLabelText(k("pack.offline.pin")), PIN);
  await user.type(within(section).getByLabelText(k("pack.offline.pin_again")), "135790");
  expect(within(section).getByText(k("pack.offline.pin_mismatch"))).toBeInTheDocument();
  expect(put).toBeDisabled();
  await user.clear(within(section).getByLabelText(k("pack.offline.pin_again")));
  await user.type(within(section).getByLabelText(k("pack.offline.pin_again")), PIN);
  await user.click(put);
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "GET", path: `/v1/cases/${CASE}/offline-packages/${COPY}/download` }));
  const link = await within(section).findByRole("link", { name: k("pack.offline.open") }, { timeout: 20_000 });
  expect(link).toHaveAttribute("href", `/cases/${CASE}/witness-box`);
  expect((await unlockCopy(CASE, PIN)).state).toBe("open");
}, 60_000);

test("removing the copy asks the server and forgets it on this device", async () => {
  const { sent, record } = servePack("launch", { copies: [copyOut({ status: "downloaded", downloaded_at: "2027-06-03T10:00:00Z" })] });
  server.use(http.delete("/v1/cases/:caseId/offline-packages/:packageId", async ({ request }) => (await record(request), new Response(null, { status: 204 }))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.offline.show") }));
  const section = await screen.findByRole("region", { name: k("pack.offline.heading") });
  await user.click(await within(section).findByRole("button", { name: k("pack.offline.delete") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "DELETE", path: `/v1/cases/${CASE}/offline-packages/${COPY}` }));
  expect(await within(section).findByText(k("pack.offline.deleted"))).toBeInTheDocument();
  expect((await findCopy(CASE)).state).toBe("none");
});

describe("the screen as designed", () => {
  test("a link back to the trial page numbers", async () => {
    servePack("launch");
    renderAt(url);
    const back = await screen.findByRole("link", { name: k("pack.back") });
    expect(back).toHaveAttribute("href", `/cases/${CASE}/trial-pages`);
  });

  test("each part says how many pages it would add, and readiness sheets can be ticked", async () => {
    const { sent, record } = servePack("launch");
    server.use(http.put("/v1/cases/:caseId/trial-pack/settings", async ({ request }) => (await record(request), etagged(settingsOut({ sections: ["report", "chronology", "readiness"] as never, version: 3 }), 3))));
    const { user } = renderAt(url);
    const report = await screen.findByRole("checkbox", { name: k("pack.section.report") });
    expect(report.closest(".av-trial-choice-row")).toHaveTextContent(fill(k("pack.pages_other"), { count: 31 }));
    expect(screen.getByRole("checkbox", { name: k("pack.section.readiness") }).closest(".av-trial-choice-row")).toHaveTextContent(fill(k("pack.pages_other"), { count: 2 }));
    await user.click(screen.getByRole("checkbox", { name: k("pack.section.readiness") }));
    await user.click(screen.getByRole("button", { name: k("pack.save") }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PUT", body: { sections: ["report", "chronology", "readiness"] } }));
  });

  test("the preview shows one readiness sheet at a time and steps between them", async () => {
    servePack("launch");
    const { user } = renderAt(url);
    const preview = await screen.findByRole("region", { name: k("pack.preview.heading") });
    expect(within(preview).getByText(fill(k("pack.preview.sheet"), { n: 1, of: 2 }))).toBeInTheDocument();
    expect(within(preview).getByText(/Did Dr P need to examine/)).toBeInTheDocument();
    expect(within(preview).getByText(k("pack.sheet.my_answer"))).toBeInTheDocument();
    expect(within(preview).getByText(k("pack.sheet.asked"))).toBeInTheDocument();
    expect(within(preview).getByRole("button", { name: k("pack.preview.previous") })).toBeDisabled();
    await user.click(within(preview).getByRole("button", { name: k("pack.preview.next") }));
    expect(within(preview).getByText(fill(k("pack.preview.sheet"), { n: 2, of: 2 }))).toBeInTheDocument();
    expect(within(preview).getByText(/Was the February result/)).toBeInTheDocument();
    expect(within(preview).getByText("p.214")).toBeInTheDocument();
    expect(within(preview).getByText(/We differ on whether/)).toBeInTheDocument();
    expect(within(preview).getByRole("button", { name: k("pack.preview.next") })).toBeDisabled();
  });

  test("with no readiness sheets there is no preview to step through", async () => {
    servePack("launch", { preview: { sections: [], total_pages: 0, sheets: [] } });
    renderAt(url);
    await screen.findByRole("heading", { level: 2, name: k("pack.title") });
    expect(screen.queryByRole("region", { name: k("pack.preview.heading") })).not.toBeInTheDocument();
  });

  test("printing the pack fetches the PDF behind the code", async () => {
    const { sent, record } = servePack("launch");
    server.use(http.get("/v1/cases/:caseId/trial-pack/latest/download", async ({ request }) => (await record(request), blobReply("application/pdf"))));
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("button", { name: k("pack.print") }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "GET", path: `/v1/cases/${CASE}/trial-pack/latest/download` }));
  });
});

test("it sits in the case frame: the case bar with the case name as the page's main heading", async () => {
  servePack("launch");
  renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: "Mrs A" })).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

describe("the preview is a sheet as the court sees it", () => {
  test("the sheet's head shows the trial number beside your page, and its table has the point and the reply", async () => {
    servePack("launch");
    renderAt(url);
    const preview = await screen.findByRole("region", { name: k("pack.preview.heading") });
    expect(within(preview).getByText(fill(k("pages.trial_ref"), { abbr: "TB", number: "2,407" }))).toBeInTheDocument();
    expect(within(preview).getByRole("columnheader", { name: k("pack.sheet.col_against") })).toBeInTheDocument();
    expect(within(preview).getByRole("columnheader", { name: k("pack.sheet.col_reply") })).toBeInTheDocument();
  });
});

describe("the bar at the bottom", () => {
  test("says how many pages it is, with Download PDF and the one main action, Print trial pack", async () => {
    servePack("launch");
    renderAt(url);
    expect(await screen.findByText(fill(k("pack.summary"), { pages: 54 }))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: k("pack.download") }).className).toMatch(/secondary/);
    expect(screen.getByRole("button", { name: k("pack.print") }).className).not.toMatch(/secondary/);
    expect(screen.queryByRole("button", { name: k("pack.rebuild") })).not.toBeInTheDocument();
  });

  test("a pack that needs rebuilding offers to make it again inside the amber notice", async () => {
    servePack("launch", { latest: { pack: packOut({ stale: true, stale_reason: "court_bundle_superseded" }), settings: settingsOut() } });
    renderAt(url);
    const notice = await screen.findByRole("alert");
    expect(within(notice).getByText(k("pack.stale"))).toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: k("pack.rebuild") })).toBeInTheDocument();
  });
});

test("a readiness sheet ends with the case, the pack and its page in the printed pack", async () => {
  servePack("launch");
  const { user } = renderAt(url);
  const preview = await screen.findByRole("region", { name: k("pack.preview.heading") });
  expect(within(preview).getByText(fill(k("pack.sheet.footer"), { case: "Mrs A", page: 40, pages: 48 }))).toBeInTheDocument();
  await user.click(within(preview).getByRole("button", { name: k("pack.preview.next") }));
  expect(within(preview).getByText(fill(k("pack.sheet.footer"), { case: "Mrs A", page: 41, pages: 48 }))).toBeInTheDocument();
});
