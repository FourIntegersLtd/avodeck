// Screen 09, the View menu: one toolbar row, and a plain list of buttons that opens above everything
// (the browser's popover, not inside the scrolling bar). Make it clearer has its own small panel.
import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
import { http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";
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

async function open() {
  mockReader(launchPack);
  const { user } = renderAt(PATH);
  const bar = await screen.findByRole("toolbar", { name: "Page view" });
  const view = within(bar).getByRole("button", { name: "View" });
  await user.click(view);
  return { user, bar, view, menu: screen.getByRole("group", { name: "View" }) };
}

describe("The View menu", () => {
  test("is not inside the toolbar, is a plain list of buttons, and puts focus on the first item", async () => {
    const { view, menu } = await open();
    expect(document.querySelector(".rd-bar")).not.toContainElement(menu);
    expect(menu).toHaveAttribute("popover");
    expect(view).toHaveAttribute("popovertarget", menu.id);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
    expect(within(menu).getAllByRole("button")[0]).toHaveFocus();
  });

  test("the arrow keys move between the items", async () => {
    const { user, menu } = await open();
    const items = within(menu).getAllByRole("button").filter((b) => !(b as HTMLButtonElement).disabled);
    await user.keyboard("{ArrowDown}");
    expect(items[1]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(items[0]).toHaveFocus();
  });

  test("Fit to width and Whole page are a pair, one chosen", async () => {
    const { user, menu, view } = await open();
    expect(within(menu).getByRole("button", { name: "Fit to width" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(menu).getByRole("button", { name: "Whole page" }));
    await user.click(view);
    const again = screen.getByRole("group", { name: "View" });
    expect(within(again).getByRole("button", { name: "Whole page" })).toHaveAttribute("aria-pressed", "true");
    expect(within(again).getByRole("button", { name: "Fit to width" })).toHaveAttribute("aria-pressed", "false");
  });

  test("picking Two pages side by side ticks it and closes the menu", async () => {
    const { user, menu, view } = await open();
    await user.click(within(menu).getByRole("button", { name: "Two pages side by side" }));
    expect(screen.queryByRole("group", { name: "View" })).not.toBeInTheDocument();
    expect(view).toHaveFocus();
    await user.click(view);
    expect(within(screen.getByRole("group", { name: "View" })).getByRole("button", { name: "Two pages side by side" })).toHaveAttribute("aria-pressed", "true");
  });

  test("Escape closes it and returns focus to View", async () => {
    const { user, view } = await open();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "View" })).not.toBeInTheDocument();
    expect(view).toHaveFocus();
  });

  test("the turning items are hidden without page.rotate", async () => {
    mockReader(launchPack);
    server.use(http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ allowed_actions: EXPERT_ACTIONS.filter((a) => a !== "page.rotate") }))));
    const { user } = renderAt(PATH);
    const bar = await screen.findByRole("toolbar", { name: "Page view" });
    await user.click(within(bar).getByRole("button", { name: "View" }));
    const menu = screen.getByRole("group", { name: "View" });
    expect(within(menu).queryByRole("button", { name: "Turn left" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("button", { name: "Upside down" })).not.toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: "Whole page" })).toBeVisible();
  });

  test("Make it clearer opens its own panel beside the page, and a chip shows while a setting is not normal", async () => {
    const { user, bar, menu } = await open();
    await user.click(within(menu).getByRole("button", { name: "Make it clearer…" }));
    expect(screen.queryByRole("group", { name: "View" })).not.toBeInTheDocument();
    const panel = screen.getByRole("group", { name: "Make it clearer" });
    expect(document.querySelector(".rd-bar")).not.toContainElement(panel);
    expect(within(bar).queryByRole("button", { name: "Clearer view on" })).not.toBeInTheDocument();
    fireEvent.change(within(panel).getByRole("slider", { name: "Brightness" }), { target: { value: "130" } });
    expect(screen.getByRole("img", { name: "Page 118" }).style.filter).toBe("brightness(1.3) contrast(1)");
    await user.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("group", { name: "Make it clearer" })).not.toBeInTheDocument();
    const chip = within(bar).getByRole("button", { name: "Clearer view on" });
    await user.click(chip);
    await user.click(within(screen.getByRole("group", { name: "Make it clearer" })).getByRole("button", { name: "Back to normal" }));
    expect(within(bar).queryByRole("button", { name: "Clearer view on" })).not.toBeInTheDocument();
  });
});

describe("The page controls on the bar", () => {
  test("› goes to the next page and ‹ to the one before", async () => {
    mockReader(launchPack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Page after" }));
    expect(await screen.findByRole("img", { name: "Page 119" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Page before" }));
    expect(await screen.findByRole("img", { name: "Page 118" })).toBeVisible();
  });

  test("a typed page number then Enter goes to that page; there is no separate Go button", async () => {
    mockReader(launchPack, { pageCount: 400 });
    const { user } = renderAt(PATH);
    const box = await screen.findByRole("textbox", { name: "Go to page" });
    expect(box).toHaveValue("118");
    expect(screen.queryByRole("button", { name: "Go" })).not.toBeInTheDocument();
    await user.clear(box);
    await user.type(box, "117{Enter}");
    expect(await screen.findByRole("img", { name: "Page 117" })).toBeVisible();
  });
});
