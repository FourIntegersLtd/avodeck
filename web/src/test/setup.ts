import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect } from "vitest";
import { resetScreens } from "../foundation/lib/screens";
import { server } from "./server";

// jsdom has no layout engine: the few browser calls Radix's menus make are stubbed.
if (typeof window !== "undefined") {
  window.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
}

// CI runs every file in parallel under coverage, where a screen can take over the 1 s default to settle.
configure({ asyncUtilTimeout: 5000 });

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// The shell's own tests (src/app) start with no slice registered, whatever slices register when they load.
beforeEach(() => {
  if (expect.getState().testPath?.includes("/src/app/")) resetScreens();
});
afterEach(async () => {
  server.resetHandlers();
  if (typeof document === "undefined") return; // node-environment guard tests (the client needs a window)
  cleanup();
  // Each test starts with nothing kept from the last.
  (await import("../foundation/lib/resourceCache")).clearResourceCache();
  document.cookie.split(";").forEach((c) => {
    const name = c.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
});
afterAll(() => server.close());
