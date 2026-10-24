// Screen 09: "More tools" is a drop-down panel anchored under its button, floating over the page (not a modal):
// the page stays visible and live while Make it clearer, Fit width and turning are adjusted.
import { fireEvent, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
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

async function open() {
  mockReader(launchPack);
  const { user } = renderAt(PATH);
  const bar = await screen.findByRole("toolbar", { name: "Page view" });
  const more = within(bar).getByRole("button", { name: "More tools" });
  await user.click(more);
  return { user, bar, more, panel: screen.getByRole("group", { name: "More tools" }) };
}

describe("The More tools drop-down", () => {
  test("floats under its button, inside the Page view bar, and does not push the page down", async () => {
    const { bar, more, panel } = await open();
    expect(bar).toContainElement(panel);
    expect(more).toHaveAttribute("aria-controls", panel.id);
    // The panel sits in a positioned wrapper with the button and is taken out of the flow.
    expect(more.parentElement).toBe(panel.parentElement);
    const css = readFileSync("src/capabilities/reader/reader.css", "utf8");
    expect(css).toMatch(/\.rd-more\s*\{[^}]*position:\s*absolute/);
    expect(css).toMatch(/\.rd-more\s*\{[^}]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.rd-more\s*\{[^}]*z-index:/);
    expect(css).toMatch(/\.rd-view__anchor\s*\{[^}]*position:\s*relative/);
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(screen.getByRole("img", { name: "Page 118" })).toBeVisible();
  });

  test("has the groups Turning, Size, View and This page", async () => {
    const { panel } = await open();
    for (const name of ["Turning", "Size", "View", "This page"]) {
      expect(within(panel).getByRole("group", { name })).toBeVisible();
    }
  });

  test("Escape closes it and returns focus to More tools", async () => {
    const { user, more } = await open();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "More tools" })).not.toBeInTheDocument();
    expect(more).toHaveFocus();
  });

  test("a click outside closes it; a click inside does not", async () => {
    const { user, panel } = await open();
    await user.click(within(panel).getByText("Size"));
    expect(screen.getByRole("group", { name: "More tools" })).toBeVisible();
    await user.click(screen.getByRole("img", { name: "Page 118" }));
    expect(screen.queryByRole("group", { name: "More tools" })).not.toBeInTheDocument();
  });

  test("the brightness and contrast sliders open inside the panel and keep it open", async () => {
    const { user, panel } = await open();
    await user.click(within(panel).getByRole("button", { name: "Make it clearer" }));
    const brightness = within(screen.getByRole("group", { name: "More tools" })).getByRole("slider", { name: "Brightness" });
    fireEvent.change(brightness, { target: { value: "130" } });
    await user.click(brightness);
    expect(screen.getByRole("group", { name: "More tools" })).toBeVisible();
    expect(screen.getByRole("img", { name: "Page 118" }).style.filter).toBe("brightness(1.3) contrast(1)");
  });

  test("Fit width, a one-shot action, closes it", async () => {
    const { user, panel } = await open();
    await user.click(within(panel).getByRole("button", { name: "Fit width" }));
    expect(screen.queryByRole("group", { name: "More tools" })).not.toBeInTheDocument();
  });
});
