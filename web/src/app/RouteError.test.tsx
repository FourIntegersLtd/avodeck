import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { isMissingScreenFile, reloadOnceForNewRelease } from "../foundation/lib/staleRelease";
import { RouteError } from "./RouteError";

function renderFailingScreen(message: string) {
  const router = createMemoryRouter(
    [{ path: "/", errorElement: <RouteError />, lazy: () => Promise.reject(new TypeError(message)) }],
    { initialEntries: ["/"] },
  );
  render(<RouterProvider router={router} />);
}

describe("a screen file missing after a new release", () => {
  beforeEach(() => window.history.replaceState(null, ""));

  it("recognises each browser's wording", () => {
    expect(isMissingScreenFile(new TypeError("Failed to fetch dynamically imported module: https://x/a.js"))).toBe(true);
    expect(isMissingScreenFile(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isMissingScreenFile(new TypeError("error loading dynamically imported module: https://x/a.js"))).toBe(true);
    expect(isMissingScreenFile(new Error("Unable to preload CSS for /assets/a.css"))).toBe(true);
    expect(isMissingScreenFile(new Error("Cannot read properties of undefined"))).toBe(false);
  });

  it("reloads once, then stops so it cannot loop", () => {
    const reload = vi.fn();
    expect(reloadOnceForNewRelease(reload)).toBe(true);
    expect(reloadOnceForNewRelease(reload)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("shows the plain error, not a reload loop, when the file is still missing after reloading", async () => {
    reloadOnceForNewRelease(() => undefined);
    renderFailingScreen("Failed to fetch dynamically imported module: https://x/a.js");
    expect(await screen.findByRole("button", { name: "Reload the page" })).toBeInTheDocument();
  });

  it("shows the plain error for any other failure", async () => {
    renderFailingScreen("Cannot read properties of undefined");
    expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
  });
});
