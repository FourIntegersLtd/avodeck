import { expect, test } from "vitest";
import { cx } from "./cx";

test("joins the class names that are set and drops the rest", () => {
  expect(cx("a", false, null, undefined, "", "b", 0 > 1 && "c")).toBe("a b");
  expect(cx()).toBe("");
});
