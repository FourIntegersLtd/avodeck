import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { StrictMode } from "react";
import { expect, test, vi } from "vitest";
import { hasMessage } from "../i18n";
import { PageViewer, PageViewToolbar } from ".";

const css = readFileSync(join(__dirname, "styles.css"), "utf8");

test("the toolbar shows every tool by default, with words from keys", () => {
  render(<PageViewer pages={[{ src: "a.png", label: "Page 1" }]} />);
  for (const name of ["Zoom out", "Zoom in", "Turn left", "Turn right", "Fit to width", "Whole page", "Back to normal"]) {
    expect(screen.getByRole("button", { name })).toBeTruthy();
  }
  expect(screen.getByLabelText("Brightness")).toBeTruthy();
  expect(screen.getByLabelText("Contrast")).toBeTruthy();
});

test("tools can be left out, and turn can be gated", () => {
  render(<PageViewer pages={[{ src: "a.png", label: "P" }]} tools={{ turn: false, clearer: false }} />);
  expect(screen.queryByRole("button", { name: "Turn left" })).toBeNull();
  expect(screen.queryByLabelText("Brightness")).toBeNull();
  expect(screen.getByRole("button", { name: "Zoom in" })).toBeTruthy();
});

test("zoom in changes the drawn width, turn rotates every stacked page", async () => {
  const user = userEvent.setup();
  render(<PageViewer pages={[{ src: "a.png", label: "Page 1" }, { src: "b.png", label: "Page 2" }]} />);
  expect(screen.getAllByRole("img")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Turn right" }));
  expect(screen.getAllByTestId("page-turn").map((e) => e.getAttribute("data-turn"))).toEqual(["90", "90"]);
  await user.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText("125%")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Turn right" })).toHaveLength(1);
});

test("a controlled turn calls onTurn and is not changed by the viewer", async () => {
  const user = userEvent.setup();
  const onTurn = vi.fn();
  render(<PageViewer pages={[{ src: "a.png", label: "P" }]} turn={0} onTurn={onTurn} />);
  await user.click(screen.getByRole("button", { name: "Turn right" }));
  expect(onTurn).toHaveBeenCalledWith(90);
  expect(screen.getByTestId("page-turn").getAttribute("data-turn")).toBe("0");
});

test("ctrl and the wheel zoom", () => {
  render(<PageViewer pages={[{ src: "a.png", label: "P" }]} />);
  fireEvent.wheel(screen.getByTestId("page-turn").closest(".av-pageview__paper")!, { ctrlKey: true, deltaY: -100 });
  expect(screen.getByText("110%")).toBeTruthy();
});

test("the toolbar is controlled and its buttons are 52px", () => {
  const onZoomIn = vi.fn();
  render(<PageViewToolbar percent={100} onZoomIn={onZoomIn} />);
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(onZoomIn).toHaveBeenCalled();
  expect(css).toMatch(/\.av-pageview__tool\s*\{[^}]*min-height:\s*var\(--button-min-height\)/);
});

test("every viewer key exists", () => {
  for (const k of ["zoom_in", "zoom_out", "zoom_level", "turn_left", "turn_right", "fit_width", "whole_page", "reset", "brightness", "contrast", "bar", "page_alt"]) {
    expect(hasMessage(`viewer.${k}`)).toBe(true);
  }
});

test("choosing a fit reports one view change, once, even in strict mode", async () => {
  const user = userEvent.setup();
  const onView = vi.fn();
  render(
    <StrictMode>
      <PageViewer pages={[{ src: "a.png", label: "Page 1" }]} onView={onView} />
    </StrictMode>,
  );
  await user.click(screen.getByRole("button", { name: "Whole page" }));
  expect(onView).toHaveBeenCalledTimes(1);
  expect(onView.mock.calls[0][0].fit).toBe("page");
});

test("the paper is a named group, and a loading page is not a picture", () => {
  render(<PageViewer pages={[{ src: null, label: "Page 1" }, { src: "b.png", label: "Page 2" }]} />);
  expect(screen.getAllByRole("group", { name: "Page 1" })).toHaveLength(1);
  expect(screen.getAllByRole("img")).toHaveLength(1);
  expect(screen.getByRole("status", { name: "Loading" })).toBeTruthy();
});

test("a page can carry an id on its paper", () => {
  render(<PageViewer pages={[{ src: "a.png", label: "Page 1", id: "page-4" }]} />);
  expect(screen.getByRole("group", { name: "Page 1" }).id).toBe("page-4");
});
