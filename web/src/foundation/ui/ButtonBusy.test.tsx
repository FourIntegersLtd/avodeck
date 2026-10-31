import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Button } from ".";

function deferred() {
  let resolve!: (v?: unknown) => void;
  let reject!: (e?: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("busy disables the button, sets aria-busy, shows a spinner and keeps the label", () => {
  const onClick = vi.fn();
  const { container } = render(<Button busy onClick={onClick}>Save</Button>);
  const b = screen.getByRole("button", { name: "Save" });
  expect(b).toBeDisabled();
  expect(b).toHaveAttribute("aria-busy", "true");
  expect(container.querySelector(".av-spinner")).not.toBeNull();
  fireEvent.click(b);
  expect(onClick).not.toHaveBeenCalled();
});

test("a button that is not busy has no spinner and no aria-busy", () => {
  const { container } = render(<Button>Save</Button>);
  expect(screen.getByRole("button")).not.toHaveAttribute("aria-busy");
  expect(container.querySelector(".av-spinner")).toBeNull();
});

test("a promise-returning onClick is busy until it resolves", async () => {
  const d = deferred();
  const onClick = vi.fn(() => d.promise);
  render(<Button onClick={onClick}>Send</Button>);
  const b = screen.getByRole("button", { name: "Send" });
  fireEvent.click(b);
  expect(b).toBeDisabled();
  expect(b).toHaveAttribute("aria-busy", "true");
  fireEvent.click(b);
  expect(onClick).toHaveBeenCalledTimes(1);
  await act(async () => { d.resolve(); await d.promise; });
  expect(b).toBeEnabled();
  expect(b).not.toHaveAttribute("aria-busy");
});

test("the button is enabled again after the handler rejects", async () => {
  const d = deferred();
  const onUnhandled = (e: PromiseRejectionEvent) => e.preventDefault();
  window.addEventListener("unhandledrejection", onUnhandled);
  render(<Button onClick={() => d.promise}>Send</Button>);
  const b = screen.getByRole("button");
  fireEvent.click(b);
  expect(b).toBeDisabled();
  await act(async () => { d.reject(new Error("no")); await new Promise((r) => setTimeout(r, 0)); });
  expect(b).toBeEnabled();
  window.removeEventListener("unhandledrejection", onUnhandled);
});

test("a handler that returns nothing never shows busy", () => {
  render(<Button onClick={() => undefined}>Go</Button>);
  const b = screen.getByRole("button");
  fireEvent.click(b);
  expect(b).toBeEnabled();
});

test("unmounting while the handler runs does not warn", async () => {
  const d = deferred();
  const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const { unmount } = render(<Button onClick={() => d.promise}>Go</Button>);
  fireEvent.click(screen.getByRole("button"));
  unmount();
  await act(async () => { d.resolve(); await d.promise; });
  expect(err).not.toHaveBeenCalled();
  err.mockRestore();
});
