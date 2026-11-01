// S16 Your report: sections from the pack, what the court requires (from the pack), citation style, build and
// rebuild, the expert's own sections, and the methodology note.
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { humanise } from "../../foundation/i18n";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { inCase, launchWords } from "../setup/testing";
import { casePack, checklistItems, etagged, reportOut, sectionKeys, sectionLabel, serve, settingsOut, V1, V2, versionOut, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/report`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("lists the pack's sections and what the court requires, and passes axe", async () => {
    serve(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 1, name: M["report.report.title"] })).toBeInTheDocument();
    for (const key of sectionKeys(run)) expect(await screen.findByText(sectionLabel(run, key))).toBeInTheDocument();
    const checklist = screen.getByRole("region", { name: M["report.report.checklist"] });
    for (const item of checklistItems(run)) expect(within(checklist).getByText(item.text ?? humanise(item.key))).toBeInTheDocument();
    expect(within(checklist).getByText(`1 of ${checklistItems(run).length} done`)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("says which sections you wrote and which were built for you, under the paper, and offers the final check", async () => {
  serve("launch");
  const { user } = renderAt(url);
  expect(await screen.findByText(M["report.report.section.written"]!)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: sectionLabel("launch", sectionKeys("launch")[1]!) }));
  expect(screen.getByText(M["report.report.section.generated"]!)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["report.report.final_check"]! })).toHaveAttribute("href", `/cases/${CASE_A}/final-check`);
  expect(screen.getByText("Draft 1 · 31 pages")).toBeInTheDocument();
});

test("before anything is built it says so, and offers to build", async () => {
  const { sent, record } = serve("launch", { report: null });
  server.use(http.post("/v1/cases/:caseId/report/build", async ({ request }) => (await record(request), json({ job_id: "j1" }, 202))));
  const { user } = renderAt(url);
  expect(await screen.findByText(M["report.report.empty"]!)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: M["report.report.final_check"]! })).toBeNull();
  await user.click(screen.getByRole("button", { name: M["report.report.build"]! }));
  await waitFor(() => expect(sent).toContainEqual({ method: "POST", path: `/v1/cases/${CASE_A}/report/build`, body: null, ifMatch: null }));
});

test("building shows a waiting message, then the new version when it is ready", async () => {
  serve("launch");
  let built = false;
  server.use(
    http.post("/v1/cases/:caseId/report/build", () => ((built = true), json({ job_id: "j1" }, 202))),
    http.get("/v1/cases/:caseId/report", () => {
      const next = versionOut({ id: V2, version_no: 2, display_name: "Draft 2" });
      return json(reportOut("launch", built ? { version: next, versions: [next, versionOut()] } : {}));
    }),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.report.rebuild"]! }));
  expect(await screen.findByText(M["report.building"]!)).toBeInTheDocument();
  expect(await screen.findByText("Draft 2 · 31 pages")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(M["report.building"]!)).toBeNull());
});

test("changing the citation style sends the version you saw and then waits for the rebuild", async () => {
  const { sent, record } = serve("launch");
  server.use(
    http.patch("/v1/cases/:caseId/report/settings", async ({ request }) => (await record(request), etagged(settingsOut({ citation_style: "paragraph", version: 4, job_id: "j2" }), 4, 202))),
  );
  const { user } = renderAt(url);
  expect(await screen.findByRole("button", { name: M["report.report.style.page"]! })).toHaveAttribute("aria-pressed", "true");
  await user.click(screen.getByRole("button", { name: M["report.report.style.paragraph"]! }));
  await waitFor(() => expect(sent).toContainEqual({ method: "PATCH", path: `/v1/cases/${CASE_A}/report/settings`, body: { citation_style: "paragraph" }, ifMatch: '"3"' }));
  expect(await screen.findByText(M["report.building"]!)).toBeInTheDocument();
});

test("you can write a section in your own words; it saves with the version you loaded", async () => {
  const { sent, record } = serve("launch");
  const key = sectionKeys("launch")[0]!;
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => etagged({ key, body: "My first words", citations: [], version: 5 }, 5)),
    http.put("/v1/cases/:caseId/report/sections/:key", async ({ request }) => (await record(request), etagged({ key, body: "Changed words", citations: [], version: 6 }, 6))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: `${M["report.report.section.edit"]} ${sectionLabel("launch", key)}` }));
  const box = await screen.findByLabelText(`Your words for ${sectionLabel("launch", key)}`);
  expect(box).toHaveValue("My first words");
  await user.clear(box);
  await user.type(box, "Changed words");
  await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
  await waitFor(() => expect(sent.at(-1)).toEqual({ method: "PUT", path: `/v1/cases/${CASE_A}/report/sections/${key}`, body: { body: "Changed words", citations: [] }, ifMatch: '"5"' }));
  expect(await screen.findByText(M["report.report.section.saved"]!)).toBeInTheDocument();
});

test("before a report is built, a section shows its empty state (no error) and does not keep asking", async () => {
  serve("launch", { report: reportOut("launch", { version: null, versions: [] }) });
  let asked = 0;
  server.use(http.get("/v1/cases/:caseId/report/paragraphs", () => (asked++, apiError(404, "not_found"))));
  renderAt(url);
  expect(await screen.findByText(M["report.report.section.empty"]!)).toBeInTheDocument();
  expect(screen.queryByText(/couldn't load/i)).toBeNull();
  await new Promise((r) => setTimeout(r, 100));
  expect(asked).toBe(1);
});

test("a section never written can still be written: the first save sends no If-Match", async () => {
  const { sent, record } = serve("launch");
  const key = sectionKeys("launch")[0]!;
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => apiError(404, "not_found")),
    http.put("/v1/cases/:caseId/report/sections/:key", async ({ request }) => (await record(request), etagged({ key, body: "New words", citations: [], version: 1 }, 1))),
  );
  const { user } = renderAt(url);
  const open = await screen.findAllByRole("button", { name: new RegExp(`${sectionLabel("launch", key)}$`) });
  await user.click(open.find((b) => /write|edit/i.test(b.getAttribute("aria-label") ?? ""))!);
  const box = await screen.findByLabelText(`Your words for ${sectionLabel("launch", key)}`);
  expect(box).toHaveValue("");
  await user.type(box, "New words");
  await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
  await waitFor(() => expect(sent.at(-1)).toEqual({ method: "PUT", path: `/v1/cases/${CASE_A}/report/sections/${key}`, body: { body: "New words", citations: [] }, ifMatch: null }));
});

const FACT = "f1111111-1111-4111-8111-111111111111";
const PAGE = "a1111111-1111-4111-8111-111111111111";
const cited = (n: number) => `[[cite:${n}]]`;
const ref = { fact_id: FACT, page_id: PAGE, quote_verbatim: "BP 150/90", fact_version: 1 };

async function openEditor(user: ReturnType<typeof renderAt>["user"], key: string) {
  await user.click(await screen.findByRole("button", { name: `${M["report.report.section.edit"]} ${sectionLabel("launch", key)}` }));
  return screen.findByLabelText(`Your words for ${sectionLabel("launch", key)}`);
}

test("a section changed in another window keeps what you typed and lets you choose", async () => {
  serve("launch");
  const key = sectionKeys("launch")[0]!;
  let gets = 0;
  const puts: Array<string | null> = [];
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => (gets++ === 0 ? etagged({ key, body: "Words", citations: [], version: 5 }, 5) : etagged({ key, body: "Their words", citations: [], version: 7 }, 7))),
    http.put("/v1/cases/:caseId/report/sections/:key", ({ request }) => {
      puts.push(request.headers.get("if-match"));
      return puts.length === 1 ? apiError(412, "stale") : etagged({ key, body: "My typed words", citations: [], version: 8 }, 8);
    }),
  );
  const { user } = renderAt(url);
  const box = await openEditor(user, key);
  await user.clear(box);
  await user.type(box, "My typed words");
  await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("My typed words")).toBeInTheDocument();
  expect(within(dialog).getByText("Their words")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: M["report.report.section.stale_yours"]! }));
  await waitFor(() => expect(puts).toEqual(['"5"', '"7"']));
  expect(await screen.findByText(M["report.report.section.saved"]!)).toBeInTheDocument();
});

test("choosing the saved words replaces what you typed", async () => {
  serve("launch");
  const key = sectionKeys("launch")[0]!;
  let gets = 0;
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => (gets++ === 0 ? etagged({ key, body: "Words", citations: [], version: 5 }, 5) : etagged({ key, body: "Their words", citations: [], version: 7 }, 7))),
    http.put("/v1/cases/:caseId/report/sections/:key", () => apiError(412, "stale")),
  );
  const { user } = renderAt(url);
  const box = await openEditor(user, key);
  await user.type(box, " more");
  await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
  await user.click(await screen.findByRole("button", { name: M["report.report.section.stale_saved"]! }));
  await waitFor(() => expect(screen.getByLabelText(`Your words for ${sectionLabel("launch", key)}`)).toHaveValue("Their words"));
});

describe("citations in a section", () => {
  const body = `Pressure was high ${cited(1)} and again ${cited(2)}.`;
  const refs = [ref, { ...ref, fact_id: "f2222222-2222-4222-8222-222222222222", quote_verbatim: "BP 160/100" }];
  const load = (key: string) => server.use(http.get("/v1/cases/:caseId/report/sections/:key", () => etagged({ key, body, citations: refs, version: 5 }, 5)));

  test("each marker is a chip linking to its fact; the text box shows no raw marker", async () => {
    serve("launch");
    const key = sectionKeys("launch")[0]!;
    load(key);
    const { user } = renderAt(url);
    const box = await openEditor(user, key);
    expect(box).not.toHaveValue(expect.stringContaining("[[cite"));
    const link = await screen.findByRole("link", { name: M["report.report.section.cite_open"]!.replace("{{number}}", "1") });
    expect(link).toHaveAttribute("href", `/cases/${CASE_A}/consultation/${FACT}`);
  });

  test("typing around a chip keeps the markers the server needs", async () => {
    const { sent, record } = serve("launch");
    const key = sectionKeys("launch")[0]!;
    load(key);
    server.use(http.put("/v1/cases/:caseId/report/sections/:key", async ({ request }) => (await record(request), etagged({ key, body, citations: refs, version: 6 }, 6))));
    const { user } = renderAt(url);
    const box = await openEditor(user, key);
    await user.type(box, " Extra");
    await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ body: `${body} Extra`, citations: refs }));
  });

  test("a citation the server will not accept is named, with a link to its fact", async () => {
    serve("launch");
    const key = sectionKeys("launch")[0]!;
    load(key);
    server.use(http.put("/v1/cases/:caseId/report/sections/:key", () => apiError(409, "not_citable", { citation: 2 })));
    const { user } = renderAt(url);
    await openEditor(user, key);
    await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
    expect(await screen.findByText(M["report.report.section.cite_refused"]!.replace("{{number}}", "2"))).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: M["report.report.section.cite_open"]!.replace("{{number}}", "2") }).length).toBeGreaterThan(0);
  });
});

describe("the preview", () => {
  let made = 0;
  const revoke = vi.fn();
  beforeEach(() => {
    made = 0;
    revoke.mockClear();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => `blob:preview-${++made}`), revokeObjectURL: revoke }));
  });
  afterEach(() => vi.unstubAllGlobals());

  test("shows the methodology note and a preview of the pages that moves on, loaded through the client as a blob", async () => {
    serve("launch");
    const asked: string[] = [];
    server.use(http.get("/v1/cases/:caseId/report/preview/:page", ({ params }) => (asked.push(String(params.page)), new Response(new Blob(["x"], { type: "image/png" }), { headers: { "content-type": "image/png" } }))));
    const { user, unmount } = renderAt(url);
    expect(await screen.findByText("1,231 of 1,231 pages read by you")).toBeInTheDocument();
    const img = await screen.findByRole("img", { name: "Page 1 of your report" });
    await waitFor(() => expect(img.getAttribute("src")).toMatch(/^blob:/));
    expect(asked).toEqual(["1"]);
    await user.click(screen.getByRole("button", { name: M["report.report.preview_next"]! }));
    await waitFor(() => expect(asked).toEqual(["1", "2"]));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:preview-1"));
    expect(screen.getByRole("img", { name: "Page 2 of your report" })).toBeInTheDocument();
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:preview-2");
    expect(V1).toBeTruthy();
  });

  test("shows the shared page tools (zoom, turn, fit) but not the clearer sliders", async () => {
    serve("launch");
    server.use(http.get("/v1/cases/:caseId/report/preview/:page", () => new Response(new Blob(["x"], { type: "image/png" }), { headers: { "content-type": "image/png" } })));
    renderAt(url);
    await screen.findByRole("img", { name: "Page 1 of your report" });
    for (const name of ["Zoom in", "Zoom out", "Turn left", "Turn right", "Fit to width", "Whole page"]) expect(screen.getByRole("button", { name })).toBeInTheDocument();
    expect(screen.queryByLabelText("Brightness")).toBeNull();
    expect(screen.queryByLabelText("Contrast")).toBeNull();
  });
});

test("lists the sections as numbered contents with a tick for each one that is done", async () => {
  const sections = sectionKeys("launch").map((key, i, all) => ({ key, written: i === 0, generated: i !== 0 && i !== all.length - 1 }));
  serve("launch", { report: reportOut("launch", { sections }) });
  renderAt(url);
  const contents = await screen.findByRole("list", { name: M["report.report.contents"]! });
  expect(contents.tagName).toBe("OL");
  const items = within(contents).getAllByRole("listitem");
  expect(items).toHaveLength(sections.length);
  expect(within(items[0]!).getByText(M["report.report.section.done"]!)).toBeInTheDocument();
  items.forEach((item, i) => expect(within(item).getByText(String(i + 1))).toBeInTheDocument());
  expect(within(items.at(-1)!).getByText(M["report.report.section.todo"]!)).toBeInTheDocument();
});

test("offers the report in Word, captions the preview with its page count and shows the pages beside the final check", async () => {
  serve("launch");
  renderAt(url);
  expect(await screen.findByRole("link", { name: M["report.report.open_word"]! })).toHaveAttribute("href", expect.stringContaining(`/v1/cases/${CASE_A}/report/versions/${V1}/download?format=docx`));
  expect(screen.getByText("Page 1 of 31")).toBeInTheDocument();
});

test("keeps what the software did, page by page, behind a Read the wording disclosure", async () => {
  serve("launch");
  renderAt(url);
  const summary = await screen.findByText(M["report.report.wording"]!);
  const details = summary.closest("details");
  expect(details).not.toBeNull();
  expect(await within(details as HTMLElement).findByText("1,231 of 1,231 pages read by you")).toBeInTheDocument();
});

test("shows how far through the opinion section you are, and only there", async () => {
  const sections = sectionKeys("launch").map((key) => ({ key, written: false, generated: true, ...(key === "opinion" ? { done: 1, total: 2 } : {}) }));
  serve("launch", { report: reportOut("launch", { sections: sections as never }) });
  renderAt(url);
  const row = await screen.findByRole("listitem", { name: sectionLabel("launch", "opinion") });
  expect(within(row).getByText("1 of 2")).toBeInTheDocument();
  const others = screen.getAllByRole("listitem").filter((li) => li !== row && li.closest("ol"));
  for (const li of others) expect(within(li).queryByText(/^\d+ of \d+$/)).toBeNull();
});

test("a section can be read as text, read-only, without the Word file", async () => {
  serve("launch", { paragraphs: [{ para_no: 1, section: "opinion", text: "The plan was written down.", question_id: null }, { para_no: 2, section: "other", text: "Not this one", question_id: null }] });
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: sectionLabel("launch", "opinion") }));
  expect(await screen.findByText("The plan was written down.")).toBeInTheDocument();
  expect(screen.queryByText("Not this one")).toBeNull();
});

test("the checklist says in words which items are done", async () => {
  serve("launch");
  renderAt(url);
  const checklist = await screen.findByRole("region", { name: M["report.report.checklist"] });
  expect(within(checklist).getAllByText(M["report.report.done"]!)).toHaveLength(1);
  expect(within(checklist).getAllByText(M["report.report.checklist_item_todo"]!)).toHaveLength(checklistItems("launch").length - 1);
});

test("the footer gives the page limit for the case's track", async () => {
  const limits = (casePack("launch") as unknown as { compliance_checklist: { page_limit: Record<string, number> } }).compliance_checklist.page_limit;
  const [track, limit] = Object.entries(limits)[0]!;
  serve("launch");
  inCase("launch", { court_track: track });
  renderAt(url);
  expect(await screen.findByText(`Draft 1 · 31 pages · page limit ${limit}`)).toBeInTheDocument();
});

describe("after the report is served (rule 8)", () => {
  test.each(["served", "withdrawn"] as const)("a %s report has no build button and points to the correct-report screen", async (status) => {
    serve("launch", { report: reportOut("launch", { version: versionOut({ status }), versions: [versionOut({ status })] }) });
    renderAt(url);
    const link = await screen.findByRole("link", { name: M["report.report.served_link"]! });
    expect(link).toHaveAttribute("href", `/cases/${CASE_A}/correct-report`);
    expect(screen.getByText(M["report.report.served"]!)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: M["report.report.rebuild"]! })).toBeNull();
    expect(screen.queryByRole("button", { name: M["report.report.build"]! })).toBeNull();
  });

  test("when the server refuses a build because the report was served, it shows the same notice", async () => {
    serve("launch");
    server.use(http.post("/v1/cases/:caseId/report/build", () => apiError(409, "served_use_supplementary")));
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("button", { name: M["report.report.rebuild"]! }));
    expect(await screen.findByRole("link", { name: M["report.report.served_link"]! })).toHaveAttribute("href", `/cases/${CASE_A}/correct-report`);
  });
});
