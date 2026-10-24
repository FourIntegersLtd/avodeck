// Screen 09, the calm redesign: a full-width quiet toolbar with the page capsule, one slim find bar,
// a footer with the one primary button, and a side that says only "Read the page first" before the read.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
import { http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetSearch } from "./search";
import { forgetView } from "./viewer";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetView();
  forgetSearch();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const box = { x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.25 };
const HITS = { 12: { hits: 3, boxes: [box] }, 40: { hits: 1, boxes: [box] }, 118: { hits: 2, boxes: [box] } };

describe("The toolbar", () => {
  test("spans the full width above the page and the side, with the page capsule and quiet tools", async () => {
    mockReader(launchPack);
    renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    const work = document.querySelector(".rd-work") as HTMLElement;
    expect(bar.compareDocumentPosition(work) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(work).not.toContainElement(bar);
    expect(within(bar).getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([
      "Page before",
      "Page after",
      "Turn page",
      "Zoom out",
      "Zoom in",
      "Find",
      "View",
      "Full screen",
    ]);
    expect(within(bar).getByRole("textbox", { name: "Go to page" })).toHaveValue("118");
    expect(within(bar).getByText("of 10")).toBeInTheDocument();
  });

  test("typing a page number and pressing Enter goes to that page", async () => {
    mockReader(launchPack, { pageCount: 300 });
    const { user } = renderAt(PATH);
    const field = await screen.findByRole("textbox", { name: "Go to page" });
    await user.clear(field);
    await user.type(field, "250{Enter}");
    expect(await screen.findByRole("img", { name: "Page 250" })).toBeVisible();
  });
});

describe("The find bar", () => {
  async function open(user: ReturnType<typeof renderAt>["user"]) {
    await screen.findByRole("img", { name: "Page 118" });
    await user.click(await screen.findByRole("button", { name: "Find" }));
    return screen.getByRole("search", { name: "Find words in the bundle" });
  }

  test("is one slim row: field, count, previous and next, Show all and Done", async () => {
    mockReader(launchPack, { searchHits: HITS });
    const { user } = renderAt(PATH);
    const bar = await open(user);
    const field = within(bar).getByRole("searchbox", { name: "Find words in the bundle" });
    expect(field).toHaveFocus();
    expect(within(bar).queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
    await user.type(field, "chest");
    expect(await within(bar).findByText("Page 3 of 3")).toBeVisible();
    expect(within(bar).getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([
      "Clear search",
      "Previous page with the words",
      "Next page with the words",
      "Show all",
      "Done",
    ]);
    expect(screen.queryByRole("list", { name: "Pages with these words" })).not.toBeInTheDocument();
  });

  test("searches as you type after a pause, once, and opens the first page with the words", async () => {
    const api = mockReader(launchPack, { searchHits: { 12: HITS[12], 40: HITS[40] } });
    const { user } = renderAt(PATH);
    const bar = await open(user);
    await user.type(within(bar).getByRole("searchbox"), "chest pain");
    expect(await screen.findByRole("img", { name: "Page 12" })).toBeVisible();
    expect(api.searches).toEqual([{ q: "chest pain" }]);
    expect(within(bar).getByText("Page 1 of 2")).toBeVisible();
    expect(screen.getAllByTestId(/^search-hit-/)).toHaveLength(1);
  });

  test("Enter goes to the next page with the words; the arrows step both ways", async () => {
    mockReader(launchPack, { searchHits: { 12: HITS[12], 40: HITS[40] } });
    const { user } = renderAt(PATH);
    const bar = await open(user);
    await user.type(within(bar).getByRole("searchbox"), "chest");
    await screen.findByRole("img", { name: "Page 12" });
    await user.type(within(bar).getByRole("searchbox"), "{Enter}");
    expect(await screen.findByRole("img", { name: "Page 40" })).toBeVisible();
    expect(within(bar).getByRole("button", { name: "Next page with the words" })).toBeDisabled();
    await user.click(within(bar).getByRole("button", { name: "Previous page with the words" }));
    expect(await screen.findByRole("img", { name: "Page 12" })).toBeVisible();
  });

  test("a slow old answer never replaces a newer one", async () => {
    mockReader(launchPack);
    const slow: Array<() => void> = [];
    server.use(
      http.post(`/v1/cases/${CASE_ID}/search`, async ({ request }) => {
        const { q } = (await request.json()) as { q: string };
        if (q === "chest") await new Promise<void>((done) => slow.push(done));
        return json({ pages: q === "chest" ? [{ page_no: 12, hits: 1, boxes: [box] }] : [{ page_no: 118, hits: 1, boxes: [box] }], total_hits: 1 });
      }),
    );
    const { user } = renderAt(PATH);
    const bar = await open(user);
    const field = within(bar).getByRole("searchbox");
    await user.type(field, "chest");
    await waitFor(() => expect(slow).toHaveLength(1));
    await user.type(field, " pain");
    expect(await within(bar).findByText("Page 1 of 1")).toBeVisible();
    slow[0]!();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
    expect(screen.queryByRole("img", { name: "Page 12" })).not.toBeInTheDocument();
  });

  test("Show all lists the pages with how many times, without a snippet; a row opens the page", async () => {
    mockReader(launchPack, { searchHits: { 12: HITS[12], 40: HITS[40] } });
    const { user } = renderAt(PATH);
    const bar = await open(user);
    await user.type(within(bar).getByRole("searchbox"), "chest");
    await screen.findByRole("img", { name: "Page 12" });
    await user.click(within(bar).getByRole("button", { name: "Show all" }));
    const list = screen.getByRole("list", { name: "Pages with these words" });
    expect(within(list).getAllByRole("link").map((a) => a.textContent)).toEqual(["Page 12 · 3 times", "Page 40 · 1 time"]);
    await user.click(within(list).getByRole("link", { name: "Page 40 · 1 time" }));
    expect(await screen.findByRole("img", { name: "Page 40" })).toBeVisible();
  });

  test("the clear button empties the field and the marks; Done closes the bar", async () => {
    mockReader(launchPack, { searchHits: HITS });
    const { user } = renderAt(PATH);
    const bar = await open(user);
    const field = within(bar).getByRole("searchbox");
    await user.type(field, "chest");
    await within(bar).findByText("Page 3 of 3");
    expect(screen.getAllByTestId(/^search-hit-/).length).toBeGreaterThan(0);
    await user.click(within(bar).getByRole("button", { name: "Clear search" }));
    expect(field).toHaveValue("");
    expect(screen.queryAllByTestId(/^search-hit-/)).toHaveLength(0);
    await user.click(within(bar).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("search", { name: "Find words in the bundle" })).not.toBeInTheDocument();
  });

  test("says so when nothing is found; one letter asks nothing of the server", async () => {
    const api = mockReader(launchPack);
    const { user } = renderAt(PATH);
    const bar = await open(user);
    const field = within(bar).getByRole("searchbox");
    await user.type(field, "a");
    expect(await within(bar).findByText("Type at least two letters or numbers.")).toBeVisible();
    expect(api.searches).toEqual([]);
    await user.type(field, "spirin");
    expect(await within(bar).findByText("We didn't find those words on any page.")).toBeVisible();
  });
});

describe("The footer and the side", () => {
  test("the footer says how many are read, and has one primary button: I've read this page", async () => {
    mockReader(launchPack);
    renderAt(PATH);
    const foot = (await screen.findByRole("button", { name: "I've read this page" })).closest("footer") as HTMLElement;
    expect(within(foot).getByText("3 of 10 read")).toBeVisible();
    expect(within(foot).getByText(/7 to go/)).toBeVisible();
    expect(within(foot).getByRole("button", { name: "Previous page" })).toBeVisible();
    const primary = Array.from(document.querySelectorAll<HTMLElement>(".av-button")).filter((b) => !b.classList.contains("av-button--secondary") && !b.classList.contains("av-button--quiet"));
    expect(primary).toHaveLength(1);
  });

  test("before the read the side shows only 'Read the page first' and one line", async () => {
    mockReader(launchPack);
    renderAt(PATH);
    const aside = await screen.findByRole("complementary", { name: "What we found on this page" });
    expect(await within(aside).findByRole("heading", { level: 2, name: "Read the page first" })).toBeVisible();
    expect(within(aside).queryByRole("list")).not.toBeInTheDocument();
    expect(within(aside).queryByText(/checked/)).not.toBeInTheDocument();
  });

  test("after the read the side has one quiet foot: Bookmark, Add a note and the hard-to-read link", async () => {
    mockReader(launchPack, { alreadyRead: true });
    renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Next page" })).toBeVisible();
    const aside = screen.getByRole("complementary", { name: "What we found on this page" });
    expect(await within(aside).findByRole("checkbox", { name: "Bookmark this page" })).toBeVisible();
    expect(within(aside).getByRole("button", { name: "Add a note" })).toBeVisible();
    const link = await within(aside).findByRole("link", { name: /3 pages we couldn't read/ });
    expect(link.closest(".rd-notes")).not.toBeNull();
  });
});
