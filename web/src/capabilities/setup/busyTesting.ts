// Shared by the setup "shows it is working" tests: a request held open until released, and the check that a button
// goes busy on the first click and a second click sends nothing more.
import { screen, waitFor } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect } from "vitest";

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

export const byName = (name: string) => screen.findByRole("button", { name });
