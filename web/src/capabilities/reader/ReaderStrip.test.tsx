// Screen 09: the page strip and the page are one surface, and the strip can be hidden so the page grows.
// The choice is held for the visit (web/AGENTS.md: no browser storage).
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { renderAt } from "../../test/render";
import { launchPack } from "../../test/fixtures";
import { forgetPages } from "./pageImageCache";
import { forgetView } from "./viewer";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetView();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;

describe("the page strip", () => {
  test("the strip and the page sit in one surface", async () => {
    mockReader(launchPack);
    renderAt(PATH);
    const strip = await screen.findByRole("listbox", { name: "Pages" });
    const page = await screen.findByTestId("page-turn");
    const surface = strip.closest(".rd-viewer");
    expect(surface).not.toBeNull();
    expect(surface!.contains(page)).toBe(true);
    expect(strip.closest(".rd-viewer")).toBe(page.closest(".rd-viewer"));
  });

  test("Hide the pages removes the strip, and Show the pages brings it back", async () => {
    mockReader(launchPack);
    const { user } = renderAt(PATH);
    await screen.findByRole("listbox", { name: "Pages" });
    await user.click(screen.getByRole("button", { name: "Hide the pages" }));
    expect(screen.queryByRole("listbox", { name: "Pages" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show the pages" }));
    expect(await screen.findByRole("listbox", { name: "Pages" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show the pages" })).not.toBeInTheDocument();
  });

  test("the [ key hides and shows the strip", async () => {
    mockReader(launchPack);
    const { user } = renderAt(PATH);
    await screen.findByRole("listbox", { name: "Pages" });
    await user.keyboard("[[");
    expect(screen.queryByRole("listbox", { name: "Pages" })).not.toBeInTheDocument();
    await user.keyboard("[[");
    expect(await screen.findByRole("listbox", { name: "Pages" })).toBeInTheDocument();
  });

  test("the choice survives opening the screen again", async () => {
    mockReader(launchPack);
    const first = renderAt(PATH);
    await screen.findByRole("listbox", { name: "Pages" });
    await first.user.click(screen.getByRole("button", { name: "Hide the pages" }));
    first.unmount();
    renderAt(PATH);
    await screen.findByRole("img", { name: "Page 118" });
    expect(screen.queryByRole("listbox", { name: "Pages" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show the pages" })).toBeInTheDocument();
  });
});
