// Screen 09, a calmer right side: the findings come first, notes and shortcuts are tucked away,
// each card has one main button, and Couldn't read sits at the foot of the side.
import { screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetSearch } from "./search";
import { forgetView } from "./viewer";
import { CASE_ID, fact } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetView();
  forgetSearch();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;

async function side() {
  const aside = await screen.findByRole("complementary", { name: "What we found on this page" });
  await screen.findByRole("article", { name: /Dr P consultation/ });
  return aside;
}

describe("the right side", () => {
  test("What we found comes first, and the notes row is last and holds the Couldn't read link", async () => {
    mockReader(launchPack, { alreadyRead: true });
    renderAt(PATH);
    const aside = await side();
    expect(aside.firstElementChild).toContainElement(within(aside).getByRole("heading", { level: 2, name: "What we found on this page" }));
    const notes = aside.querySelector(".rd-notes");
    expect(notes).not.toBeNull();
    expect(aside.lastElementChild).toBe(notes);
    expect(within(notes as HTMLElement).getByRole("link", { name: /pages we couldn't read/ })).toBeVisible();
  });

  test("notes are closed by default and open on a click; the bookmark is a checkbox", async () => {
    const api = mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const aside = await side();
    const row = await within(aside).findByRole("button", { name: "Add a note" });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(within(aside).queryByRole("textbox", { name: "Your note on this page" })).not.toBeInTheDocument();
    const mark = within(aside).getByRole("checkbox", { name: "Bookmark this page" });
    expect(mark).not.toBeChecked();
    await user.click(mark);
    await vi.waitFor(() => expect(mark).toBeChecked());
    expect(api.noteSaves).toEqual([{ page: 118, body: { bookmarked: true, note: "" } }]);
    await user.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(within(aside).getByRole("textbox", { name: "Your note on this page" })).toBeVisible();
  });

  test("a page with a note says so on the row", async () => {
    mockReader(launchPack, { alreadyRead: true, notes: [{ page_no: 118, bookmarked: false, note: "Check the dose" }] });
    renderAt(PATH);
    const aside = await side();
    expect(await within(aside).findByRole("button", { name: "Your note on this page" })).toHaveAttribute("aria-expanded", "false");
  });

  test("there is no keyboard block on the side; the ? key opens Keyboard shortcuts in More tools", async () => {
    mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const aside = await side();
    expect(within(aside).queryByText("Keyboard, if you like it")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
    await user.keyboard("?");
    const panel = screen.getByRole("group", { name: "View" });
    const keys = within(panel).getByRole("group", { name: "Keyboard shortcuts" });
    expect(within(keys).getByText("C")).toBeVisible();
    expect(within(keys).getByText(/not right/)).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "View" })).not.toBeInTheDocument();
  });

  test("Keyboard shortcuts is also an item in the More tools panel", async () => {
    mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    await side();
    await user.click(screen.getByRole("button", { name: "View" }));
    const item = within(screen.getByRole("group", { name: "View" })).getByRole("button", { name: "Keyboard shortcuts" });
    expect(item).toHaveAttribute("aria-expanded", "false");
    await user.click(item);
    expect(screen.getByRole("group", { name: "Keyboard shortcuts" })).toBeVisible();
  });

  test("Couldn't read sits at the foot of the side, not in the bottom bar", async () => {
    mockReader(launchPack, { alreadyRead: true });
    renderAt(PATH);
    const aside = await side();
    const link = await within(aside).findByRole("link", { name: /3 pages we couldn't read/ });
    expect(link).toHaveAttribute("href", `/cases/${CASE_ID}/read/couldnt-read`);
    const foot = document.querySelector(".rd-foot") as HTMLElement;
    expect(within(foot).queryByRole("link", { name: /couldn't read/ })).not.toBeInTheDocument();
  });

  test("a page never sorted says we're still reading it, not that nothing was found", async () => {
    mockReader(launchPack, { alreadyRead: true, facts: [], sourceType: null });
    renderAt(PATH);
    expect(await screen.findByText("We're still reading this page. Its suggestions will appear here.")).toBeVisible();
    expect(screen.queryByText("We found nothing to check on this page.")).not.toBeInTheDocument();
  });

  test("a sorted page with no suggestions still says nothing was found", async () => {
    mockReader(launchPack, { alreadyRead: true, facts: [] });
    renderAt(PATH);
    expect(await screen.findByText("We found nothing to check on this page.")).toBeVisible();
    expect(screen.queryByText(/still reading this page/)).not.toBeInTheDocument();
  });
});

describe("a fact card", () => {
  const primary = (c: HTMLElement) => within(c).getAllByRole("button").filter((b) => b.classList.contains("av-button") && !/av-button--/.test(b.className));

  test("has exactly one main button, Correct; Not right and Change a detail are quiet and at least 52px", async () => {
    mockReader(launchPack, { alreadyRead: true });
    renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(primary(c).map((b) => b.textContent)).toEqual(["Correct"]);
    for (const name of ["Not right", "Change a detail"]) expect(within(c).getByRole("button", { name })).toHaveClass("av-button--quiet");
    const css = readFileSync("src/capabilities/reader/reader.css", "utf8");
    expect(css).toMatch(/\.rd-actions--quiet[^{]*\.av-button[^{]*\{[^}]*min-height:\s*52px/);
  });

  test("the timeline date and the clearer copy wait behind More for this fact", async () => {
    mockReader(launchPack, { alreadyRead: true, facts: [fact(1, { status: "verified", event_date: null })], clearer: { page: 900, read: true, words: [] } });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(within(c).queryByRole("button", { name: "Set date on timeline" })).not.toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Move to the clearer copy" })).not.toBeInTheDocument();
    const more = within(c).getByRole("button", { name: "More for this fact" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(within(c).getByRole("button", { name: "Set date on timeline" })).toBeVisible();
    expect(within(c).getByRole("button", { name: "Move to the clearer copy" })).toBeVisible();
  });

  test("keys 3 and 1 still open the change form and correct", async () => {
    const api = mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.keyboard("3");
    expect(await within(c).findByRole("button", { name: "Save change" })).toBeVisible();
    await user.click(within(c).getByRole("button", { name: "Cancel" }));
    await user.keyboard("1");
    await vi.waitFor(() => expect(api.verifies[0]).toMatchObject({ body: { action: "correct" } }));
  });
});
