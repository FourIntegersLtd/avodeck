// Screen 08, Your bundle is ready: the bundle summary from the API, in the pack's source words and
// colours, with a card for each thing the expert should know. Runs under both pack sets.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { humanise } from "../../foundation/i18n";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, missingItem, packWords, summary, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle`;

function mock(pack: Schemas["CasePack"], opts: { summary?: Partial<Schemas["BundleSummary"]>; item?: Schemas["Case"] } = {}) {
  signedIn({ pack });
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(opts.item ?? theCase())),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack, opts.summary))),
    http.get(`/v1/cases/${CASE_ID}/missing-items`, () =>
      json({
        missing_items: [
          missingItem(),
          missingItem({ id: "m1000000-0000-4000-8000-000000000002", description: "Practice task list after January", status: "requested" }),
          missingItem({ id: "m1000000-0000-4000-8000-000000000003", description: "An item already received", status: "received" }),
        ],
      }),
    ),
  );
}

const card = (text: string | RegExp | HTMLElement) => (text instanceof HTMLElement ? text : screen.getByText(text)).closest(".av-card") as HTMLElement;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Bundle ready under the $name", ({ pack }) => {
  const words = packWords(pack);
  const sources = pack.source_types as Array<{ key: string; colour: string }>;

  test("shows how many pages there are and where they came from, in the pack's words and colours", async () => {
    mock(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" })).toBeVisible();
    expect(screen.getByText("1,231 pages, numbered 1 to 1,231.")).toBeVisible();
    expect(screen.getByText("Sorted by source and date. Each page keeps its number for good. Anything sent later is numbered after page 1,231.")).toBeVisible();
    const list = screen.getByRole("list", { name: "Where the pages came from" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(sources.length);
    sources.forEach((s, i) => expect(items[i]).toHaveTextContent(words.sources[s.key] ?? humanise(s.key)));
    const fills = Array.from(container.querySelectorAll("svg rect")).map((r) => r.getAttribute("fill")?.toUpperCase());
    sources.forEach((s) => expect(fills).toContain(s.colour.toUpperCase()));
    expect(await axeViolations(container)).toEqual([]);
  });

  test("copies, pages that may belong to someone else, and pages we couldn't read each get a card", async () => {
    mock(pack);
    renderAt(PATH);
    const copies = card(await screen.findByText("37 copies set aside. Nothing deleted."));
    expect(within(copies).getByRole("link", { name: "See them" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle/copies`);
    const other = card("4 pages may belong to someone else.");
    expect(other).toHaveTextContent("pages 412 to 415");
    expect(within(other).getByRole("link", { name: "Check them" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle/not-this-patient`);
    expect(card("31 pages we couldn't read.")).toHaveTextContent("Mostly handwriting and faint scans. We'll show you these one by one.");
  });

  test("recordings and pages in another language are listed", async () => {
    mock(pack);
    renderAt(PATH);
    const kept = card(await screen.findByText("1 voice recording and 1 page not in English."));
    expect(kept).toHaveTextContent("The recording is kept but not read.");
    expect(kept).toHaveTextContent("The page (p.903, Welsh) may need a certified translation.");
  });

  test("the card for kept recordings has the board's grey icon tile", async () => {
    mock(pack);
    renderAt(PATH);
    const kept = card(await screen.findByText("1 voice recording and 1 page not in English."));
    expect(kept.querySelector(".av-icon-tile--grey")).not.toBeNull();
  });

  test("what looks missing is listed, with the pack's words for asking", async () => {
    mock(pack);
    renderAt(PATH);
    const missing = card(await screen.findByText("2 things look missing"));
    expect(within(missing).getByText("Colonoscopy report from September")).toBeVisible();
    expect(within(missing).getByText("Practice task list after January")).toBeVisible();
    expect(within(missing).queryByText("An item already received")).toBeNull();
    expect(missing).toHaveTextContent("There is also 1 stretch of time with no records.");
    expect(within(missing).getByRole("link", { name: words.askLabel })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle/ask`);
  });

  test("a file we couldn't open gets its own card, with the reason, and the way to ask for a readable copy", async () => {
    const [first, second] = summary(pack).documents as [Schemas["BundleSummary"]["documents"][number], Schemas["BundleSummary"]["documents"][number]];
    mock(pack, {
      summary: {
        documents: [
          first,
          { ...second, pages: 0, first_page_no: null, last_page_no: null, unreadable_reason: "locked" },
          { ...second, id: "d1000000-0000-4000-8000-000000000009", filename: "scan.pdf", pages: 0, first_page_no: null, last_page_no: null, unreadable_reason: "damaged" },
        ],
      },
    });
    renderAt(PATH);
    const files = card(await screen.findByText("2 files we couldn't open."));
    expect(files).toHaveTextContent("hospital-notes.pdf is password-protected.");
    expect(files).toHaveTextContent("scan.pdf looks damaged.");
    expect(files).toHaveTextContent("The other files are not affected.");
    expect(within(files).getByRole("link", { name: words.askLabel })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle/ask`);
  });

  test("the files table shows each file, its pages, how it was obtained and when it arrived", async () => {
    mock(pack);
    renderAt(PATH);
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("Where it came from");
    expect(rows[0]).toHaveTextContent("How it was obtained");
    expect(rows[1]).toHaveTextContent("practice-records.pdf");
    expect(rows[1]).toHaveTextContent("600");
    expect(rows[1]).toHaveTextContent("Subject access request");
    expect(rows[2]).toHaveTextContent("Not given");
  });

  test("later deliveries are listed with their page numbers", async () => {
    mock(pack, {
      summary: {
        versions: [
          { bundle_version: 1, documents: 2, pages: 1231, first_page_no: 1, last_page_no: 1231, added_at: "2026-10-02T09:00:00Z" },
          { bundle_version: 2, documents: 1, pages: 40, first_page_no: 1232, last_page_no: 1271, added_at: "2026-10-09T09:00:00Z" },
        ],
      },
    });
    renderAt(PATH);
    expect(await screen.findByText("Delivery 2: pages 1,232 to 1,271")).toBeVisible();
  });

  test("Start reading is the one main action", async () => {
    mock(pack);
    renderAt(PATH);
    const start = await screen.findByRole("link", { name: "Start reading" });
    expect(start).toHaveClass("av-button");
    expect(screen.getAllByRole("link").filter((l) => l.classList.contains("av-button") && !/--(secondary|quiet|danger)/.test(l.className))).toHaveLength(1);
  });

  test("with nothing to flag there are no cards for copies, other people's pages or missing items", async () => {
    mock(pack, { summary: { duplicates: 0, unreadable: 0, gaps: 0, missing_items: 0, quarantine_suggestions: [], exhibits: [], foreign_language: [] } });
    server.use(http.get(`/v1/cases/${CASE_ID}/missing-items`, () => json({ missing_items: [] })));
    renderAt(PATH);
    await screen.findByText("1,231 pages, numbered 1 to 1,231.");
    expect(screen.queryByText(/copies set aside/)).toBeNull();
    expect(screen.queryByText(/may belong to someone else/)).toBeNull();
    expect(screen.queryByText(/look missing/)).toBeNull();
    expect(screen.queryByText(/couldn't read/)).toBeNull();
    expect(screen.queryByText(/couldn't open/)).toBeNull();
  });

  test("someone who cannot write to the instructing party is not offered it", async () => {
    mock(pack, { item: theCase({ allowed_actions: VIEWER_ACTIONS }) });
    renderAt(PATH);
    await screen.findByText("2 things look missing");
    expect(screen.queryByRole("link", { name: words.askLabel })).toBeNull();
    expect(screen.queryByRole("link", { name: "Check them" })).toBeNull();
  });

  test("the sources are one tall stack of pages with a label and page count for each source", async () => {
    mock(pack);
    renderAt(PATH);
    await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" });
    const stack = screen.getByRole("img", { name: "Where the pages came from" });
    expect(stack.tagName.toLowerCase()).toBe("svg");
    expect(stack.querySelectorAll("rect").length).toBeGreaterThanOrEqual(sources.length);
    const labels = within(screen.getByRole("list", { name: "Where the pages came from" })).getAllByRole("listitem");
    expect(labels).toHaveLength(sources.length);
    expect(labels[0]!.querySelector(".av-mono")).toHaveTextContent(/^[\d,]+$/);
  });

  test("each card has an icon tile, and the cards that need a look are amber notices", async () => {
    mock(pack);
    renderAt(PATH);
    const other = card(await screen.findByText("4 pages may belong to someone else."));
    expect(other.querySelector(".av-icon-tile--amber")).not.toBeNull();
    expect(other).toHaveClass("av-notice--amber");
    expect(card("37 copies set aside. Nothing deleted.").querySelector(".av-icon-tile--mint")).not.toBeNull();
    expect(card("1,231 pages, numbered 1 to 1,231.").querySelector(".av-icon-tile")).not.toBeNull();
  });

  test("the files table has no heading above it, and Start reading carries an arrow", async () => {
    mock(pack);
    renderAt(PATH);
    await screen.findByRole("table");
    expect(screen.getByRole("heading", { name: "Your files" })).toHaveClass("av-visually-hidden");
    expect(screen.getByRole("link", { name: "Start reading" }).querySelector("svg")).not.toBeNull();
  });

  test("the unreadable card says what the pages mostly are", async () => {
    mock(pack);
    renderAt(PATH);
    expect(await screen.findByText("Mostly handwriting and faint scans. We'll show you these one by one.")).toBeVisible();
  });

  test("pages we turned the right way up are counted in plain words", async () => {
    mock(pack, { summary: { straightened: 3 } });
    renderAt(PATH);
    expect(await screen.findByText("3 pages turned the right way up.")).toBeVisible();
  });

  test("no card when no page was turned", async () => {
    mock(pack, { summary: { straightened: 0 } });
    renderAt(PATH);
    await screen.findByRole("table");
    expect(screen.queryByText(/turned the right way up/)).toBeNull();
  });
});
