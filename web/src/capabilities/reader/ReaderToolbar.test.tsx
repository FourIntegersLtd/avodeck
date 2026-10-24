// Screen 09's page toolbar (approved 2026-10-24): one calm row with the tools used most (Turn page, zoom,
// Find words, More tools); the rest sit under "More tools", grouped. Finding words shows its results in a
// clear strip, opens the first page with the words and steps through them. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetSearch } from "./search";
import { forgetView } from "./viewer";
import { CASE_ID, EXPERT_ACTIONS, theCase } from "./testData";
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
const ELSEWHERE = { 12: { hits: 3, boxes: [box] }, 40: { hits: 1, boxes: [box] }, 41: { hits: 2, boxes: [box] } };

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("The page toolbar under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("shows only Turn page, zoom, Find words and More tools; the rest wait under More tools", async () => {
    mockReader(pack);
    const { user } = renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    expect(within(bar).getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([
      "Turn page",
      "Zoom out",
      "Zoom in",
      "Find words",
      "More tools",
    ]);
    for (const name of ["Two pages", "Full screen", "Fit width", "Whole page", "Make it clearer", "Clearer copy", "Turn left", "Upside down", "Undo turn"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "Not this patient" })).not.toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "Find words in the bundle" })).not.toBeInTheDocument();

    const more = within(bar).getByRole("button", { name: "More tools" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("group", { name: "More tools" });
    expect(within(panel).getByRole("group", { name: "Turning" })).toBeVisible();
    for (const name of ["Turn left", "Upside down", "Undo turn", "Fit width", "Whole page", "Make it clearer", "Two pages", "Full screen", "Clearer copy"]) {
      expect(within(panel).getByRole("button", { name })).toBeVisible();
    }
    expect(within(panel).getByRole("link", { name: "Not this patient" })).toBeVisible();
    // Go to page stays on show, beside the source.
    expect(screen.getByRole("textbox", { name: "Go to page" })).toBeVisible();
  });

  test("Turn page turns the page a quarter clockwise at once and saves it", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    await screen.findByRole("img", { name: "Page 118" });
    await user.click(within(bar).getByRole("button", { name: "Turn page" }));
    expect(screen.getByTestId("page-turn")).toHaveAttribute("data-turn", "90");
    await waitFor(() => expect(api.turns).toEqual([{ page: 118, body: { degrees: 90 } }]));
  });

  test("More tools closes on Escape and once a tool is picked", async () => {
    mockReader(pack);
    const { user } = renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    const more = within(bar).getByRole("button", { name: "More tools" });
    await user.click(more);
    expect(screen.getByRole("group", { name: "More tools" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "More tools" })).not.toBeInTheDocument();
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveFocus();

    await user.click(more);
    await user.click(within(screen.getByRole("group", { name: "More tools" })).getByRole("button", { name: "Two pages" }));
    expect(screen.queryByRole("group", { name: "More tools" })).not.toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Page 119" })).toBeVisible();
  });

  test("someone who may not turn pages sees no turn controls, on the bar or under More tools", async () => {
    mockReader(pack);
    server.use(http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ allowed_actions: EXPERT_ACTIONS.filter((a) => a !== "page.rotate") }))));
    const { user } = renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    expect(within(bar).queryByRole("button", { name: "Turn page" })).not.toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "More tools" }));
    const panel = screen.getByRole("group", { name: "More tools" });
    expect(within(panel).queryByRole("group", { name: "Turning" })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Turn left" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Fit width" })).toBeVisible();
  });

  describe("finding words", () => {
    async function search(user: ReturnType<typeof renderAt>["user"], q: string) {
      const bar = await screen.findByRole("toolbar", { name: "Page view" });
      await user.click(within(bar).getByRole("button", { name: "Find words" }));
      const input = screen.getByRole("searchbox", { name: "Find words in the bundle" });
      expect(input).toHaveFocus();
      await user.type(input, q);
      await user.click(screen.getByRole("button", { name: "Find" }));
    }

    test("opens the first page with the words, and Next and Previous step through the pages found", async () => {
      mockReader(pack, { searchHits: ELSEWHERE });
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await search(user, "chest pain");
      expect(await screen.findByRole("img", { name: "Page 12" })).toBeVisible();
      const strip = screen.getByRole("region", { name: "Pages with these words" });
      expect(within(strip).getByText("Found on 3 pages")).toBeVisible();
      expect(within(strip).getByRole("link", { name: "Page 12, 3 times" })).toHaveAttribute("aria-current", "page");
      expect(screen.getAllByTestId(/^search-hit-/)).toHaveLength(1);
      const previous = within(strip).getByRole("button", { name: "Previous page with the words" });
      expect(previous).toBeDisabled();
      await user.click(within(strip).getByRole("button", { name: "Next page with the words" }));
      expect(await screen.findByRole("img", { name: "Page 40" })).toBeVisible();
      await user.click(within(screen.getByRole("region", { name: "Pages with these words" })).getByRole("button", { name: "Next page with the words" }));
      expect(await screen.findByRole("img", { name: "Page 41" })).toBeVisible();
      const atLast = screen.getByRole("region", { name: "Pages with these words" });
      expect(within(atLast).getByRole("button", { name: "Next page with the words" })).toBeDisabled();
      await user.click(within(atLast).getByRole("button", { name: "Previous page with the words" }));
      expect(await screen.findByRole("img", { name: "Page 40" })).toBeVisible();
    });

    test("stays on this page when the words are on it", async () => {
      mockReader(pack, { searchHits: { ...ELSEWHERE, 118: { hits: 1, boxes: [box] } } });
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await search(user, "chest");
      expect(await screen.findByText("Found on 4 pages")).toBeVisible();
      expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
      expect(screen.getAllByTestId(/^search-hit-/)).toHaveLength(1);
    });

    test("the Find button says it is looking while the server searches", async () => {
      mockReader(pack);
      let answer: () => void = () => undefined;
      server.use(
        http.post(`/v1/cases/${CASE_ID}/search`, async () => {
          await new Promise<void>((done) => (answer = done));
          return json({ pages: [], total_hits: 0 });
        }),
      );
      const { user } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await search(user, "aspirin").catch(() => undefined);
      const looking = await screen.findByRole("button", { name: "Finding…" });
      expect(looking).toBeDisabled();
      answer();
      expect(await screen.findByText("We didn't find those words on any page.")).toBeVisible();
      expect(screen.getByRole("button", { name: "Find" })).toBeEnabled();
    });

    test("with More tools and the results open, passes axe", async () => {
      mockReader(pack, { searchHits: { ...ELSEWHERE, 118: { hits: 1, boxes: [box] } } });
      const { user, container } = renderAt(PATH);
      await screen.findByRole("img", { name: "Page 118" });
      await search(user, "chest");
      await screen.findByText("Found on 4 pages");
      await user.click(screen.getByRole("button", { name: "More tools" }));
      expect(await axeViolations(container)).toEqual([]);
    });
  });
});
