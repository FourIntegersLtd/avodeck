// Case home: the papers the expert uploaded, grouped by kind, and where they are in the case.
// Runs under both pack sets; the steps and the kind words come from the pack or the words file.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { formatDate } from "./format";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, summary, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/home`;
const doc = (id: number, kind: string, filename: string, pages: number | null, first: number | null, how: string | null, on: string | null) => ({
  id: `d2000000-0000-4000-8000-00000000000${id}`,
  kind,
  filename,
  pages,
  first_page_no: first,
  last_page_no: first && pages ? first + pages - 1 : null,
  received_on: on,
  obtained_how: how,
  bundle_version: 1,
});
const DOCS = [
  doc(1, "letter", "letter.pdf", 6, null, "Uploaded by you", "2026-10-02"),
  doc(2, "records", "practice.pdf", 1204, 1, "Sent by the instructing party", "2026-10-02"),
  doc(3, "records", "hospital.pdf", 512, 1205, null, null),
  doc(4, "other", "witness.pdf", 22, 1717, "Uploaded by you", "2026-10-02"),
] as unknown as Schemas["BundleSummary"]["documents"];

function mock(pack: Schemas["CasePack"], opts: { empty?: boolean; stage?: string; read?: number; total?: number; questions?: number } = {}) {
  signedIn({ pack });
  const steps = pack.workflow_steps as Array<{ key: string }>;
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json({ ...theCase(), stage: opts.stage ?? steps[1]!.key })),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack, { documents: opts.empty ? [] : DOCS, ...(opts.empty ? { pages: 0 } : {}), duplicates: 38, unreadable: 6 }))),
    http.get(`/v1/cases/${CASE_ID}/progress`, () => json({ read: opts.read ?? 412, total: opts.total ?? 1742, seconds_left: 0 })),
    http.get(`/v1/cases/${CASE_ID}/steps/progress`, () => json({ steps: [{ key: steps[1]!.key, done: 412, total: 1742, percent: 24 }] })),
    http.get(`/v1/cases/${CASE_ID}/questions`, () => json({ questions: Array.from({ length: opts.questions ?? 4 }, (_, i) => ({ id: String(i) })), assumptions: [], locked: false, instruction_version_id: null })),
  );
}

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Case home under the $name", ({ pack }) => {
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;

  test("no documents yet: the call to action is to add the patient's records, not more papers", async () => {
    mock(pack, { empty: true });
    renderAt(PATH);
    expect(await screen.findByRole("link", { name: "Add the patient's records" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Add more papers" })).toBeNull();
  });

  test("lists the papers in groups by kind, each with its files, pages and how they arrived", async () => {
    mock(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Your papers for this case" })).toBeVisible();
    expect(screen.getByText("Everything you were sent, as it arrived. Open any file to see its pages.")).toBeVisible();
    const records = screen.getByRole("region", { name: "Medical records" });
    expect(records).toHaveTextContent(/2 files · 1,716 pages/);
    expect(within(records).getByRole("button", { name: /Medical records/ })).toHaveAttribute("aria-expanded", "true");
    expect(within(records).getByText("practice.pdf")).toBeVisible();
    expect(within(records).getByText("Sent by the instructing party")).toBeVisible();
    expect(within(records).getByText("1,204 pages")).toBeVisible();
    expect(records).toHaveTextContent(formatDate("2026-10-02", pack.locale));
    expect(screen.getByRole("region", { name: "Letter of instruction" })).toHaveTextContent("1 file · 6 pages");
    expect(screen.getByText("Pages keep the numbers they were given, even when you add more papers later.")).toBeVisible();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("a group folds shut and open", async () => {
    mock(pack);
    const { user } = renderAt(PATH);
    const records = await screen.findByRole("region", { name: "Medical records" });
    await user.click(within(records).getByRole("button", { name: /Medical records/ }));
    expect(within(records).queryByText("practice.pdf")).toBeNull();
    expect(within(records).getByRole("button", { name: /Medical records/ })).toHaveAttribute("aria-expanded", "false");
    await user.click(within(records).getByRole("button", { name: /Medical records/ }));
    expect(within(records).getByText("practice.pdf")).toBeVisible();
  });

  test("Open goes to the file's first page in the reader; a file with no numbered pages has no link", async () => {
    mock(pack);
    renderAt(PATH);
    const records = await screen.findByRole("region", { name: "Medical records" });
    const links = within(records).getAllByRole("link", { name: /^Open/ });
    expect(links[0]).toHaveAttribute("href", `/cases/${CASE_ID}/read/1`);
    expect(links[1]).toHaveAttribute("href", `/cases/${CASE_ID}/read/1205`);
    const letter = screen.getByRole("region", { name: "Letter of instruction" });
    expect(within(letter).queryByRole("link", { name: /^Open/ })).toBeNull();
  });

  test("Where you are shows the pack's steps, the pages read and one main button", async () => {
    mock(pack);
    renderAt(PATH);
    const card = await screen.findByRole("region", { name: "Where you are" });
    expect(within(card).getAllByRole("listitem")).toHaveLength(steps.length);
    const current = within(card).getAllByRole("listitem").filter((li) => li.getAttribute("aria-current") === "step");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("24%");
    expect(within(card).getByRole("progressbar")).toBeInTheDocument();
    expect(card).toHaveTextContent("You've read 412 of 1,742 pages.");
    const main = within(card).getAllByRole("link");
    expect(main).toHaveLength(1);
    if (steps[1]!.screens?.includes("reader")) {
      expect(main[0]).toHaveTextContent("Carry on reading from page 413");
      expect(main[0]).toHaveAttribute("href", `/cases/${CASE_ID}/read/413`);
    }
  });

  test("About the case shows the due date, questions, copies set aside and pages we couldn't read", async () => {
    mock(pack);
    renderAt(PATH);
    const card = await screen.findByRole("region", { name: "About the case" });
    expect(await within(card).findByText("4")).toBeVisible();
    expect(card).toHaveTextContent("Report due");
    expect(card).toHaveTextContent(formatDate("2026-11-30", pack.locale));
    expect(card).toHaveTextContent("Questions you were asked");
    expect(card).toHaveTextContent("Copies set aside38 pages");
    expect(card).toHaveTextContent("Pages we couldn't read6");
  });

  test("the case name in the bar links to the case home", async () => {
    mock(pack);
    renderAt(PATH);
    const bar = await screen.findByRole("banner");
    expect(await within(bar).findByRole("link", { name: "Mrs A" })).toHaveAttribute("href", PATH);
  });
  test("Add more papers opens the add-papers screen when the person may upload", async () => {
    mock(pack);
    renderAt(PATH);
    expect(await screen.findByRole("link", { name: "Add more papers" })).toHaveAttribute("href", `/cases/${CASE_ID}/papers/add`);
  });

  test("Add more papers is not shown to someone who may not upload", async () => {
    mock(pack);
    server.use(http.get(`/v1/cases/${CASE_ID}`, () => json({ ...theCase({ allowed_actions: VIEWER_ACTIONS }), stage: (pack.workflow_steps as Array<{ key: string }>)[1]!.key })));
    renderAt(PATH);
    await screen.findByRole("heading", { name: "Your papers for this case" });
    expect(screen.queryByRole("link", { name: "Add more papers" })).toBeNull();
  });
});
