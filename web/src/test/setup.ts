import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect } from "vitest";
import { resetScreens } from "../foundation/lib/screens";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// The shell's own tests (src/app) start with no slice registered, whatever slices register when they load.
beforeEach(() => {
  if (expect.getState().testPath?.includes("/src/app/")) resetScreens();
});
afterEach(() => {
  server.resetHandlers();
  if (typeof document !== "undefined") cleanup();
  if (typeof document === "undefined") return; // node-environment guard tests
  document.cookie.split(";").forEach((c) => {
    const name = c.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
});
afterAll(() => server.close());
