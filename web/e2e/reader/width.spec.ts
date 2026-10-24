// The reader uses the whole window on a wide screen: the side reaches the right edge, with no empty strip beside it.
import { expect, test } from "@playwright/test";
import { CASE_A, PACKS, stubReader } from "./helpers";

test("09 the side reaches the right edge of a wide window", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await stubReader(page, PACKS[0]!.pack);
  await page.goto(`/cases/${CASE_A}/read/118`);
  await expect(page.getByRole("button", { name: "I've read this page" })).toBeVisible();
  const side = (await page.locator(".rd-side").first().boundingBox())!;
  expect(side.x + side.width).toBeGreaterThan(1900);
});
