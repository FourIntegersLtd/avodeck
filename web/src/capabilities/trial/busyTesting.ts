// Shared by the "shows it is working" tests: a request held open until released, the check that a button goes
// busy on the first click and a second click sends nothing more, and the same for a file chooser.
import { waitFor } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect } from "vitest";
import { pdf } from "./testing";

export function hold() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let count = 0;
  return { release, count: () => count, enter: async () => { count += 1; await gate; } };
}

export async function expectBusyOnce(user: UserEvent, button: HTMLElement, h: { count: () => number }) {
  await user.click(button);
  await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
  expect(button).toBeDisabled();
  await user.click(button);
  expect(h.count()).toBe(1);
}

/** A file chosen: the chooser goes busy (disabled, aria-busy) while the upload runs and takes no second file. */
export async function expectUploadBusyOnce(user: UserEvent, input: HTMLElement, h: { count: () => number }) {
  await user.upload(input, pdf("one.pdf"));
  await waitFor(() => expect(input).toHaveAttribute("aria-busy", "true"));
  expect(input).toBeDisabled();
  await user.upload(input, pdf("two.pdf"));
  expect(h.count()).toBe(1);
}
