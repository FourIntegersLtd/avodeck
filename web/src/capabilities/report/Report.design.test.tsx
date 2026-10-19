// S16 as drawn on the board: a numbered list of sections at the left, the chosen section as a paper page in the
// middle, what the court requires and the methodology note at the right, one main action at the bottom right.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, caseItem, launchWords } from "../setup/testing";
import { checklistItems, reportOut, sectionKeys, sectionLabel, serve, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/report`;
const paragraphs = (key: string) => [
  { para_no: 1, section: key, text: "First cited paragraph.", question_id: null },
  { para_no: 2, section: key, text: "Second cited paragraph.", question_id: null },
];

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the citation choice is a switch beside Open in Word, and the screen is axe-clean", async () => {
    const [first] = sectionKeys(run);
    serve(run, { paragraphs: paragraphs(first!) });
    const { container } = renderAt(url);
    const group = await screen.findByRole("radiogroup", { name: M["report.report.style"]! });
    expect(group).toHaveClass("av-segmented");
    expect(within(group).getByRole("radio", { name: M["report.report.style.page"]! })).toBeChecked();
    expect(screen.getByRole("link", { name: M["report.report.open_word"]! })).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the sections are a numbered list, and the first one's words sit on the paper page", async () => {
    const [first, second] = sectionKeys(run);
    serve(run, { paragraphs: paragraphs(first!) });
    renderAt(url);
    const contents = await screen.findByRole("list", { name: M["report.report.contents"]! });
    const items = within(contents).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent(`1 ${sectionLabel(run, first!)}`);
    expect(items[1]).toHaveTextContent(`2 ${sectionLabel(run, second!)}`);
    expect(within(items[0]!).getByRole("button", { name: sectionLabel(run, first!) })).toHaveAttribute("aria-current", "true");
    const paper = await screen.findByRole("region", { name: M["report.report.paper"]! });
    expect(within(paper).getByRole("heading", { name: `1. ${sectionLabel(run, first!)}` })).toBeInTheDocument();
    expect(await within(paper).findByText("First cited paragraph.")).toBeInTheDocument();
    expect(within(paper).getByText("1.1")).toBeInTheDocument();
    expect(within(paper).getByText("1.2")).toBeInTheDocument();
  });

  test("the appendices are named in one line under the sections, from the pack", async () => {
    serve(run);
    renderAt(url);
    const names = ((casePack(run).report_appendices ?? []) as Array<{ label?: string }>).map((a) => a.label).join(", ");
    expect(await screen.findByText(`Appendices: ${names}`)).toBeInTheDocument();
  });
});

test("choosing another section puts its words on the paper page", async () => {
  const [first, second] = sectionKeys("launch");
  serve("launch", { paragraphs: [...paragraphs(first!), { para_no: 1, section: second!, text: "Words of the second section.", question_id: null }] });
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: sectionLabel("launch", second!) }));
  const paper = screen.getByRole("region", { name: M["report.report.paper"]! });
  expect(await within(paper).findByText("Words of the second section.")).toBeInTheDocument();
  expect(within(paper).queryByText("First cited paragraph.")).toBeNull();
  expect(screen.getByRole("button", { name: sectionLabel("launch", second!) })).toHaveAttribute("aria-current", "true");
});

test("a section with no text yet says nothing is cited, and the way to write it is under the paper", async () => {
  const [first] = sectionKeys("launch");
  serve("launch");
  renderAt(url);
  const paper = await screen.findByRole("region", { name: M["report.report.paper"]! });
  expect(await within(paper).findByText(M["report.report.section.empty"]!)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: `${M["report.report.section.edit"]} ${sectionLabel("launch", first!)}` })).toBeInTheDocument();
});

test("a to-do section says so in small words; a section part-way through shows how far", async () => {
  const sections = sectionKeys("launch").map((key, i) => ({ key, written: false, generated: i > 1, ...(i === 0 ? { done: 2, total: 4 } : {}) }));
  serve("launch", { report: reportOut("launch", { sections: sections as never }) });
  renderAt(url);
  const items = within(await screen.findByRole("list", { name: M["report.report.contents"]! })).getAllByRole("listitem");
  expect(within(items[0]!).getByText("2 of 4")).toBeInTheDocument();
  expect(within(items[1]!).getByText(M["report.report.section.todo"]!)).toBeInTheDocument();
  expect(within(items[2]!).getByText(M["report.report.section.done"]!)).toBeInTheDocument();
});

test("what the court requires marks each item done or still to do, and the count sits under them", async () => {
  serve("launch");
  renderAt(url);
  const card = await screen.findByRole("region", { name: M["report.report.checklist"]! });
  const rows = within(card).getAllByRole("listitem");
  expect(rows).toHaveLength(checklistItems("launch").length);
  expect(within(rows[0]!).getByText(M["report.report.checklist_item_done"]!)).toBeInTheDocument();
  expect(within(rows[1]!).getByText(M["report.report.checklist_item_todo"]!)).toBeInTheDocument();
  expect(rows[1]).toHaveClass("rp-need--todo");
});

test("the methodology note is shown in full with the wording behind a link, and counts with separators", async () => {
  serve("launch");
  renderAt(url);
  const card = await screen.findByRole("region", { name: M["report.report.methodology"]! });
  expect(within(card).getByText(M["report.report.methodology_body"]!)).toBeInTheDocument();
  const summary = within(card).getByText(M["report.report.wording"]!);
  expect(summary.closest("summary")).toHaveClass("av-text-link");
  expect(await within(card).findByText("1,231 of 1,231 pages read by you")).toBeInTheDocument();
});

test("the bottom bar gives the page count and one main action with an arrow", async () => {
  serve("launch");
  renderAt(url);
  const run = await screen.findByRole("link", { name: M["report.report.final_check"]! });
  expect(run.querySelector("svg")).not.toBeNull();
  expect(run).not.toHaveClass("av-button--secondary");
  expect(screen.getByText("Draft 1 · 31 pages")).toBeInTheDocument();
});

test.each([
  ["intermediate_track", "Draft 1 · 31 pages · page limit 20"],
  ["some_other_track", "Draft 1 · 31 pages · no page limit"],
])("the footer names the page limit from the pack for the case's court track (%s)", async (track, line) => {
  serve("launch");
  server.use(http.get("/v1/cases/:caseId", () => json(caseItem("launch", { court_track: track, instructing_party_type: null }), 200)));
  renderAt(url);
  expect(await screen.findByText(line)).toBeInTheDocument();
});

test("the footer names the court track the server gives, before the page limit", async () => {
  serve("launch", { report: reportOut("launch", { court_track_label: "multi-track" }) });
  server.use(http.get("/v1/cases/:caseId", () => json(caseItem("launch", { court_track: "some_other_track", instructing_party_type: null }), 200)));
  renderAt(url);
  expect(await screen.findByText("Draft 1 · 31 pages · multi-track, no page limit")).toBeInTheDocument();
});
