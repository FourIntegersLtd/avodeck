// S16 Your report: sections from the pack, what the court requires (from the pack), citation style, build and
// rebuild, the expert's own sections, and the methodology note.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { humanise } from "../../foundation/i18n";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { checklistItems, etagged, reportOut, sectionKeys, sectionLabel, serve, settingsOut, V1, V2, versionOut, type PackRun } from "./testing";
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

test("says which sections you wrote and which were built for you, and offers the final check", async () => {
  serve("launch");
  renderAt(url);
  const first = await screen.findByRole("listitem", { name: sectionLabel("launch", sectionKeys("launch")[0]!) });
  expect(within(first).getByText(M["report.report.section.written"]!)).toBeInTheDocument();
  const second = screen.getByRole("listitem", { name: sectionLabel("launch", sectionKeys("launch")[1]!) });
  expect(within(second).getByText(M["report.report.section.generated"]!)).toBeInTheDocument();
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
  expect(await screen.findByRole("radio", { name: M["report.report.style.page"]! })).toBeChecked();
  await user.click(screen.getByRole("radio", { name: M["report.report.style.paragraph"]! }));
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

test("a section changed in another window asks you to reload instead of overwriting", async () => {
  serve("launch");
  const key = sectionKeys("launch")[0]!;
  server.use(
    http.get("/v1/cases/:caseId/report/sections/:key", () => etagged({ key, body: "Words", citations: [], version: 5 }, 5)),
    http.put("/v1/cases/:caseId/report/sections/:key", () => apiError(412, "stale")),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: `${M["report.report.section.edit"]} ${sectionLabel("launch", key)}` }));
  await screen.findByLabelText(`Your words for ${sectionLabel("launch", key)}`);
  await user.click(screen.getByRole("button", { name: M["report.report.section.save"]! }));
  expect(await screen.findByText(M["report.stale"]!)).toBeInTheDocument();
});

test("shows the methodology note and a preview of the pages that moves on", async () => {
  serve("launch");
  const { user } = renderAt(url);
  expect(await screen.findByText("1231 of 1231 pages read by you")).toBeInTheDocument();
  const img = await screen.findByRole("img", { name: "Page 1 of your report" });
  expect(img).toHaveAttribute("src", expect.stringContaining(`/v1/cases/${CASE_A}/report/preview/1`));
  await user.click(screen.getByRole("button", { name: M["report.report.preview_next"]! }));
  expect(screen.getByRole("img", { name: "Page 2 of your report" })).toBeInTheDocument();
  expect(V1).toBeTruthy();
});

test("lists the sections as numbered contents with a tick for each one that is done", async () => {
  const sections = sectionKeys("launch").map((key, i, all) => ({ key, written: i === 0, generated: i !== 0 && i !== all.length - 1 }));
  serve("launch", { report: reportOut("launch", { sections }) });
  renderAt(url);
  const contents = await screen.findByRole("list", { name: M["report.report.contents"]! });
  expect(contents.tagName).toBe("OL");
  const items = within(contents).getAllByRole("listitem");
  expect(items).toHaveLength(sections.length);
  expect(within(items[0]!).getByText("✓")).toBeInTheDocument();
  expect(within(items.at(-1)!).getByText("○")).toBeInTheDocument();
  expect(within(items.at(-1)!).getByText(M["report.report.section.todo"]!)).toBeInTheDocument();
});

test("offers the report in Word, captions the preview with its page count and shows the pages beside the final check", async () => {
  serve("launch");
  renderAt(url);
  expect(await screen.findByRole("link", { name: M["report.report.open_word"]! })).toHaveAttribute("href", expect.stringContaining(`/v1/cases/${CASE_A}/report/versions/${V1}/download?format=docx`));
  expect(screen.getByText("Page 1 of 31")).toBeInTheDocument();
  expect(screen.getByText("31 pages")).toBeInTheDocument();
});

test("keeps the how-you-used-Avodeck wording behind a Read the wording disclosure", async () => {
  serve("launch");
  renderAt(url);
  const summary = await screen.findByText(M["report.report.wording"]!);
  const details = summary.closest("details");
  expect(details).not.toBeNull();
  expect(within(details as HTMLElement).getByText(M["report.report.methodology_body"]!)).toBeInTheDocument();
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
  await user.click(await screen.findByRole("button", { name: `${M["report.report.read_text"]} ${sectionLabel("launch", "opinion")}` }));
  expect(await screen.findByText("The plan was written down.")).toBeInTheDocument();
  expect(screen.queryByText("Not this one")).toBeNull();
});
