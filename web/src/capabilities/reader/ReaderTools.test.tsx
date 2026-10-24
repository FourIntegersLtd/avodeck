// Screen 09's page viewer, batch 2 (approved 2026-10-23): finding words in the bundle, two pages side by
// side, full screen, and the expert's own bookmarks and private notes. The server searches only the words
// read from the pages (never the suggestions); searching is not reading. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetView } from "./viewer";
import { forgetSearch } from "./search";
import { CASE_ID, pageWords } from "./testData";
import { mockReader, moreTools, openFind } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetView();
  forgetSearch();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const HITS = {
  12: { hits: 3, boxes: [{ x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.25 }] },
  118: { hits: 2, boxes: [{ x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.12 }, { x0: 0.5, y0: 0.6, x1: 0.7, y1: 0.62 }] },
};

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("The page viewer's tools under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  describe("finding words in the bundle", () => {
    test("lists the pages with the words and marks them on the page, without reading it", async () => {
      const api = mockReader(pack, { searchHits: HITS });
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await openFind(user);
      const find = screen.getByRole("search", { name: "Find words in the bundle" });
      await user.type(within(find).getByRole("searchbox", { name: "Find words in the bundle" }), "chest pain");
      await user.click(within(find).getByRole("button", { name: "Find" }));
      expect(await screen.findByText("Found on 2 pages")).toBeVisible();
      expect(api.searches).toEqual([{ q: "chest pain" }]);
      const results = screen.getByRole("list", { name: "Pages with these words" });
      expect(within(results).getAllByRole("link").map((a) => a.textContent)).toEqual(["Page 12, 3 times", "Page 118, 2 times"]);
      // The hits on this page are marked over the picture.
      const marks = screen.getAllByTestId(/^search-hit-/);
      expect(marks).toHaveLength(2);
      expect(marks[0]).toHaveStyle({ left: "10%", top: "10%", width: "10%" });
      // Searching is not reading: no suggestions asked for, the page is still to be marked as read.
      expect(api.factRequests).toBe(0);
      expect(api.reads).toEqual([]);
      expect(screen.getByRole("button", { name: "Mark as read" })).toBeVisible();
    });

    test("a page from the list opens with its hits marked; Clear search takes the marks away", async () => {
      mockReader(pack, { searchHits: HITS });
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await openFind(user);
      await user.type(screen.getByRole("searchbox", { name: "Find words in the bundle" }), "chest pain");
      await user.click(screen.getByRole("button", { name: "Find" }));
      await user.click(await screen.findByRole("link", { name: "Page 12, 3 times" }));
      expect(await screen.findByRole("img", { name: "Page 12" })).toBeVisible();
      expect(screen.getAllByTestId(/^search-hit-/)).toHaveLength(1);
      // The list stays for the visit, so the expert can go on to the next page with the words.
      expect(screen.getByRole("link", { name: "Page 118, 2 times" })).toBeVisible();
      await user.click(screen.getByRole("button", { name: "Clear search" }));
      expect(screen.queryAllByTestId(/^search-hit-/)).toHaveLength(0);
      expect(screen.queryByRole("list", { name: "Pages with these words" })).not.toBeInTheDocument();
    });

    test("says so when nothing is found, and asks for at least two letters before asking the server", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await openFind(user);
      const box = screen.getByRole("searchbox", { name: "Find words in the bundle" });
      await user.type(box, "a");
      await user.click(screen.getByRole("button", { name: "Find" }));
      expect(await screen.findByText("Type at least two letters or numbers.")).toBeVisible();
      expect(api.searches).toEqual([]);
      await user.type(box, "spirin");
      await user.click(screen.getByRole("button", { name: "Find" }));
      expect(await screen.findByText("We didn't find those words on any page.")).toBeVisible();
    });
  });

  describe("two pages side by side", () => {
    test("shows the next page beside this one; any page number can be chosen", async () => {
      mockReader(pack);
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      const two = async () => within(await moreTools(user)).getByRole("button", { name: "Two pages" });
      expect(await two()).toHaveAttribute("aria-pressed", "false");
      await user.click(await two());
      expect(await two()).toHaveAttribute("aria-pressed", "true");
      await user.keyboard("{Escape}");
      expect(await screen.findByRole("img", { name: "Page 119" })).toBeVisible();
      const beside = screen.getByRole("textbox", { name: "Page beside it" });
      await user.clear(beside);
      await user.type(beside, "40");
      await user.click(screen.getByRole("button", { name: "Show" }));
      expect(await screen.findByRole("img", { name: "Page 40" })).toBeVisible();
      expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
      await user.click(await two());
      expect(screen.queryByRole("img", { name: "Page 40" })).not.toBeInTheDocument();
    });

    test("a page with a clearer copy shows the copy beside it", async () => {
      mockReader(pack, { clearer: { page: 1201, read: false, words: pageWords() } });
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Two pages" }));
      expect(await screen.findByRole("img", { name: "Page 1201" })).toBeVisible();
    });
  });

  describe("full screen", () => {
    test("the Full screen button and F show only the page view; Escape comes back", async () => {
      mockReader(pack);
      const { user, container } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Full screen" }));
      expect(container.querySelector(".rd")).toHaveClass("rd--full");
      expect(within(await moreTools(user)).getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "true");
      // The first Escape closes More tools; the next leaves full screen.
      await user.keyboard("{Escape}");
      expect(container.querySelector(".rd")).toHaveClass("rd--full");
      await user.keyboard("{Escape}");
      expect(container.querySelector(".rd")).not.toHaveClass("rd--full");
      await user.keyboard("f");
      expect(container.querySelector(".rd")).toHaveClass("rd--full");
      await user.keyboard("f");
      expect(container.querySelector(".rd")).not.toHaveClass("rd--full");
      // Not while typing.
      await user.type(screen.getByRole("textbox", { name: "Go to page" }), "f");
      expect(container.querySelector(".rd")).not.toHaveClass("rd--full");
    });
  });

  describe("your bookmarks and private notes", () => {
    test("bookmarking a page and keeping a note saves them for you only, without reading the page", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      const mark = await screen.findByRole("button", { name: "Bookmark this page" });
      expect(mark).toHaveAttribute("aria-pressed", "false");
      await user.click(mark);
      await waitFor(() => expect(mark).toHaveAttribute("aria-pressed", "true"));
      expect(screen.getByText("Only you see your bookmarks and notes. They are not part of the records.")).toBeVisible();
      await user.type(screen.getByRole("textbox", { name: "Your note on this page" }), "Ask about the missing letter");
      await user.click(screen.getByRole("button", { name: "Save note" }));
      expect(await screen.findByText("Note saved.")).toBeVisible();
      expect(api.noteSaves).toEqual([
        { page: 118, body: { bookmarked: true, note: "" } },
        { page: 118, body: { bookmarked: true, note: "Ask about the missing letter" } },
      ]);
      expect(api.reads).toEqual([]);
      expect(api.factRequests).toBe(0);
    });

    test("your bookmarks list opens each page, and a page shows its note when opened", async () => {
      mockReader(pack, { notes: [{ page_no: 12, bookmarked: true, note: "" }, { page_no: 118, bookmarked: false, note: "Check the dose" }] });
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      expect(await screen.findByRole("textbox", { name: "Your note on this page" })).toHaveValue("Check the dose");
      await user.click(screen.getByRole("button", { name: "Your bookmarks and notes (2)" }));
      const list = screen.getByRole("list", { name: "Your bookmarks and notes" });
      await user.click(within(list).getByRole("link", { name: /Page 12/ }));
      expect(await screen.findByRole("img", { name: "Page 12" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Bookmark this page" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("textbox", { name: "Your note on this page" })).toHaveValue("");
    });

    test("every tool open passes axe", async () => {
      mockReader(pack, { searchHits: HITS, notes: [{ page_no: 12, bookmarked: true, note: "" }] });
      const { user, container } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      await openFind(user);
      await user.type(screen.getByRole("searchbox", { name: "Find words in the bundle" }), "chest");
      await user.click(screen.getByRole("button", { name: "Find" }));
      await screen.findByText("Found on 2 pages");
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Two pages" }));
      await screen.findByRole("img", { name: "Page 119" });
      await user.click(await screen.findByRole("button", { name: "Your bookmarks and notes (1)" }));
      expect(await axeViolations(container)).toEqual([]);
    });
  });
});
