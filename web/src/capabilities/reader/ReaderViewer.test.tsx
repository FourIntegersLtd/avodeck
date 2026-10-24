// Screen 09's page viewer (approved 2026-10-23, items 1 to 4): turning the page at once, fit and zoom
// that stay as the expert left them, going to a page, the page strip, and "Make it clearer".
// The view settings are held for the visit (no browser storage). Runs under both pack sets.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetView } from "./viewer";
import { CASE_ID, EXPERT_ACTIONS, theCase } from "./testData";
import { mockReader, moreTools } from "./testing";
import { json, http, server } from "../../test/api";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetView();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const turned = () => screen.getByTestId("page-turn");

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("The page viewer under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  describe("turning the page", () => {
    test("Turn page, Turn left and Upside down turn the page at once and save how it is turned", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      expect(turned()).toHaveAttribute("data-turn", "0");

      await user.click(within(bar).getByRole("button", { name: "Turn page" }));
      expect(turned()).toHaveAttribute("data-turn", "90");
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Upside down" }));
      expect(turned()).toHaveAttribute("data-turn", "270");
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Turn left" }));
      expect(turned()).toHaveAttribute("data-turn", "180");

      await waitFor(() => expect(api.turns.map((t) => t.body)).toEqual([{ degrees: 90 }, { degrees: 270 }, { degrees: 180 }]));
      expect(api.turns.every((t) => t.page === 118)).toBe(true);
      // Nothing is read again: the old "turn and read again" call is never made.
      expect(api.rotations).toEqual([]);
      expect(screen.queryByText(/reading the page again/)).not.toBeInTheDocument();
    });

    test("Undo turn puts back the turn before, one step at a time", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      const undo = async () => within(await moreTools(user)).getByRole("button", { name: "Undo turn" });
      expect(await undo()).toBeDisabled();
      await user.keyboard("{Escape}");
      await user.click(within(bar).getByRole("button", { name: "Turn page" }));
      await user.click(within(bar).getByRole("button", { name: "Turn page" }));
      await user.click(await undo());
      expect(turned()).toHaveAttribute("data-turn", "90");
      await user.click(await undo());
      expect(turned()).toHaveAttribute("data-turn", "0");
      expect(await undo()).toBeDisabled();
      await waitFor(() => expect(api.turns.map((t) => t.body.degrees)).toEqual([90, 180, 90, 0]));
    });

    test("T turns right, Shift+T turns left and U undoes; not while typing", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await user.keyboard("t");
      expect(turned()).toHaveAttribute("data-turn", "90");
      await user.keyboard("t");
      await user.keyboard("{Shift>}T{/Shift}");
      expect(turned()).toHaveAttribute("data-turn", "90");
      await user.keyboard("u");
      expect(turned()).toHaveAttribute("data-turn", "180");
      await user.type(screen.getByRole("textbox", { name: "Go to page" }), "t");
      expect(turned()).toHaveAttribute("data-turn", "180");
      await waitFor(() => expect(api.turns).toHaveLength(4));
    });

    test("a page turned before opens turned", async () => {
      mockReader(pack, { turn: 270 });
      renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await waitFor(() => expect(turned()).toHaveAttribute("data-turn", "270"));
    });

    test("the highlights and the word picker turn with the page, inside the same turned box", async () => {
      mockReader(pack, { alreadyRead: true });
      const { user } = renderAt(PATH);
      await screen.findByRole("article", { name: /Dr P consultation/ });
      await user.keyboard("t");
      for (const box of screen.getAllByTestId(/^highlight-/)) expect(turned()).toContainElement(box);
      expect(turned()).toContainElement(screen.getByRole("img", { name: "Page 118" }));
      await user.click(screen.getByRole("button", { name: "Change a detail" }));
      const word = await screen.findByRole("button", { name: "visit" });
      expect(turned()).toContainElement(word);
      // The boxes keep their page fractions: the turn is drawn, never written into them.
      expect(screen.getAllByTestId(/^highlight-/)[0]!.style.left).toBe("6%");
    });

    test("someone who may not turn pages sees no turn buttons", async () => {
      mockReader(pack);
      server.use(http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ allowed_actions: EXPERT_ACTIONS.filter((a) => a !== "page.rotate") }))));
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      expect(within(bar).queryByRole("button", { name: "Turn page" })).not.toBeInTheDocument();
      expect(within(await moreTools(user)).getByRole("button", { name: "Fit width" })).toBeVisible();
    });
  });

  describe("fit and zoom", () => {
    test("Fit width is the start; Whole page and the zoom buttons change it and say which is on", async () => {
      mockReader(pack);
      const { user } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      const width = async () => within(await moreTools(user)).getByRole("button", { name: "Fit width" });
      const whole = async () => within(await moreTools(user)).getByRole("button", { name: "Whole page" });
      expect(await width()).toHaveAttribute("aria-pressed", "true");
      await user.click(await whole());
      expect(await whole()).toHaveAttribute("aria-pressed", "true");
      expect(await width()).toHaveAttribute("aria-pressed", "false");
      await user.click(within(bar).getByRole("button", { name: "Zoom in" }));
      expect(within(bar).getByText("125%")).toBeVisible();
      expect(await whole()).toHaveAttribute("aria-pressed", "false");
    });

    test("Ctrl and the mouse wheel zoom the page", async () => {
      mockReader(pack);
      renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await screen.findByRole("img", { name: "Page 118" });
      const paper = document.querySelector(".rd-paper")!;
      fireEvent.wheel(paper, { deltaY: -100, ctrlKey: true });
      expect(within(bar).getByText("110%")).toBeVisible();
      fireEvent.wheel(paper, { deltaY: 100, ctrlKey: true });
      fireEvent.wheel(paper, { deltaY: 100, ctrlKey: true });
      expect(within(bar).getByText("90%")).toBeVisible();
      fireEvent.wheel(paper, { deltaY: 100 }); // without Ctrl the page just scrolls
      expect(within(bar).getByText("90%")).toBeVisible();
    });

    test("the zoom stays as it was on the next page, and after leaving and coming back", async () => {
      mockReader(pack, { alreadyRead: true, lastPage: 119 });
      const { user, unmount } = renderAt(PATH);
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await user.click(within(bar).getByRole("button", { name: "Zoom in" }));
      await user.click(within(bar).getByRole("button", { name: "Zoom in" }));
      await screen.findByRole("article", { name: /Dr P consultation/ });
      await user.keyboard("{ArrowRight}");
      await screen.findByRole("img", { name: "Page 119" });
      expect(within(screen.getByRole("toolbar", { name: "Page view" })).getByText("150%")).toBeVisible();
      unmount();
      renderAt(PATH);
      expect(within(await screen.findByRole("toolbar", { name: "Page view" })).getByText("150%")).toBeVisible();
    });
  });

  describe("going to a page", () => {
    test("Go to page opens the page asked for", async () => {
      mockReader(pack, { pageCount: 400 });
      const { user } = renderAt(PATH);
      await user.type(await screen.findByRole("textbox", { name: "Go to page" }), "250{Enter}");
      expect(await screen.findByRole("img", { name: "Page 250" })).toBeVisible();
    });

    test("a page that is not in the bundle says so and stays put", async () => {
      mockReader(pack, { pageCount: 400 });
      const { user } = renderAt(PATH);
      await user.type(await screen.findByRole("textbox", { name: "Go to page" }), "5000{Enter}");
      expect(await screen.findByText("There's no page 5000 in this bundle.")).toBeVisible();
      expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
    });
  });

  describe("the page strip", () => {
    test("4,000 pages load in blocks and only the pages in view are drawn", async () => {
      const api = mockReader(pack, { pageCount: 4000, readPages: [117] });
      renderAt(PATH);
      const strip = await screen.findByRole("listbox", { name: "Pages" });
      await waitFor(() => expect(api.listRequests.filter((q) => q.includes("limit=1000"))).toHaveLength(4));
      const options = within(strip).getAllByRole("option");
      expect(options.length).toBeGreaterThan(0);
      expect(options.length).toBeLessThan(60);
      expect(within(strip).getByRole("option", { name: "Page 118, not read yet" })).toHaveAttribute("aria-selected", "true");
      expect(within(strip).getByRole("option", { name: "Page 117, read" })).toBeInTheDocument();
      // Small pictures only, and only for the pages drawn.
      await waitFor(() => expect(api.smallImages).toBeGreaterThan(0));
      expect(api.smallImages).toBeLessThanOrEqual(options.length);
    });

    test("the strip works with the keyboard: arrows move, Enter opens, and the page does not change until then", async () => {
      mockReader(pack, { pageCount: 4000 });
      const { user } = renderAt(PATH);
      const strip = await screen.findByRole("listbox", { name: "Pages" });
      await within(strip).findByRole("option", { name: "Page 118, not read yet" });
      strip.focus();
      await user.keyboard("{ArrowDown}{ArrowDown}");
      const active = document.getElementById(strip.getAttribute("aria-activedescendant")!)!;
      expect(active).toHaveAccessibleName("Page 120, not read yet");
      expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
      await user.keyboard("{ArrowUp}{Enter}");
      expect(await screen.findByRole("img", { name: "Page 119" })).toBeVisible();
    });

    test("after scrolling the strip with the mouse, the keyboard carries on from a page in view", async () => {
      mockReader(pack, { pageCount: 4000 });
      const { user } = renderAt(PATH);
      const strip = await screen.findByRole("listbox", { name: "Pages" });
      await within(strip).findByRole("option", { name: "Page 118, not read yet" });
      strip.scrollTop = 2000 * 136;
      fireEvent.scroll(strip);
      // The active page is one that is drawn, so a screen reader can say it.
      const shown = () => document.getElementById(strip.getAttribute("aria-activedescendant")!);
      expect(shown()).toHaveAccessibleName("Page 2001, not read yet");
      strip.focus();
      await user.keyboard("{ArrowDown}");
      expect(shown()).toHaveAccessibleName("Page 2002, not read yet");
    });

    test("Home and End go to the first and last pages of the bundle", async () => {
      mockReader(pack, { pageCount: 4000 });
      const { user } = renderAt(PATH);
      const strip = await screen.findByRole("listbox", { name: "Pages" });
      await within(strip).findByRole("option", { name: "Page 118, not read yet" });
      strip.focus();
      await user.keyboard("{End}");
      expect(document.getElementById(strip.getAttribute("aria-activedescendant")!)).toHaveAccessibleName("Page 4000, not read yet");
      await user.keyboard("{Home}");
      expect(document.getElementById(strip.getAttribute("aria-activedescendant")!)).toHaveAccessibleName("Page 1, not read yet");
    });
  });

  describe("Make it clearer", () => {
    test("brightness and contrast change only how the picture is shown, and Back to normal undoes it", async () => {
      const api = mockReader(pack);
      const { user } = renderAt(PATH);
      await screen.findByRole("toolbar", { name: "Page view" });
      const img = await screen.findByRole("img", { name: "Page 118" });
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Make it clearer" }));
      fireEvent.change(screen.getByRole("slider", { name: "Brightness" }), { target: { value: "130" } });
      fireEvent.change(screen.getByRole("slider", { name: "Contrast" }), { target: { value: "160" } });
      expect(img.style.filter).toBe("brightness(1.3) contrast(1.6)");
      // Only the picture: the highlights keep their own colours.
      expect(turned().style.filter).toBe("");
      await user.click(screen.getByRole("button", { name: "Back to normal" }));
      expect(img.style.filter).toBe("");
      expect(api.turns).toEqual([]);
      expect(api.patches).toEqual([]);
    });

    test("the viewer with every control open passes axe", async () => {
      mockReader(pack, { pageCount: 200 });
      const { user, container } = renderAt(PATH);
      await screen.findByRole("toolbar", { name: "Page view" });
      await within(await screen.findByRole("listbox", { name: "Pages" })).findByRole("option", { name: "Page 118, not read yet" });
      await user.click(within(await moreTools(user)).getByRole("button", { name: "Make it clearer" }));
      expect(await axeViolations(container)).toEqual([]);
    });
  });
});
