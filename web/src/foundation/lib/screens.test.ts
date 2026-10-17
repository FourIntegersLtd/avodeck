import { expect, test } from "vitest";
import { hasScreen, registerScreen, resetScreens, screenPath } from "./screens";

test("resetScreens forgets every registered screen", () => {
  registerScreen("x", () => "/x");
  expect(screenPath("x")).toBe("/x");
  resetScreens();
  expect(hasScreen("x")).toBe(false);
  expect(screenPath("x")).toBeNull();
});
