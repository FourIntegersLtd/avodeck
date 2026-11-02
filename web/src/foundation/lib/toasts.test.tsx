import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ToastProvider, useToast } from "./toasts";

function Fire({ action }: { action?: boolean }) {
  const show = useToast();
  return <button onClick={() => show({ text: "Saved it", action: action ? { label: "Open your papers", to: "/x" } : undefined })}>fire</button>;
}

function setup(action = true) {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  const view = render(
    <MemoryRouter>
      <ToastProvider>
        <Fire action={action} />
      </ToastProvider>
    </MemoryRouter>,
  );
  return { user, ...view };
}

describe("toasts", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  test("a toast goes after 12 seconds", async () => {
    const { user } = setup(false);
    await user.click(screen.getByRole("button", { name: "fire" }));
    expect(screen.getByText("Saved it")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(12001));
    expect(screen.queryByText("Saved it")).not.toBeInTheDocument();
  });

  test("a toast with an action stays while the pointer is over it", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "fire" }));
    await user.hover(screen.getByRole("link", { name: "Open your papers" }));
    act(() => void vi.advanceTimersByTime(30000));
    expect(screen.getByText("Saved it")).toBeInTheDocument();
    await user.unhover(screen.getByRole("link", { name: "Open your papers" }));
    act(() => void vi.advanceTimersByTime(12001));
    expect(screen.queryByText("Saved it")).not.toBeInTheDocument();
  });

  test("a toast with an action stays while focus is inside it", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "fire" }));
    act(() => screen.getByRole("link", { name: "Open your papers" }).focus());
    act(() => void vi.advanceTimersByTime(30000));
    expect(screen.getByText("Saved it")).toBeInTheDocument();
  });

  test("closing clears the timer, and the live region is always there", async () => {
    const { user } = setup();
    const region = screen.getByRole("region", { name: "Notifications" });
    await user.click(screen.getByRole("button", { name: "fire" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.getByRole("region", { name: "Notifications" })).toBe(region);
  });

  test("unmounting clears the timers", async () => {
    const { user, unmount } = setup();
    await user.click(screen.getByRole("button", { name: "fire" }));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
