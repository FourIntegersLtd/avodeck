// Hiding the page strip gives the page more room (screen 09).
import { expect, test } from "@playwright/test";
import { CASE_A, PACKS, stubReader } from "./helpers";

test("09 Hide the pages makes the page wider", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stubReader(page, PACKS[0]!.pack);
  await page.goto(`/cases/${CASE_A}/read/118`);
  await expect(page.getByRole("button", { name: "I've read this page" })).toBeVisible();
  const before = (await page.locator(".rd-paper").first().boundingBox())!;
  await page.getByRole("button", { name: "Hide the pages" }).click();
  await expect(page.getByRole("listbox", { name: "Pages" })).toHaveCount(0);
  const after = (await page.locator(".rd-paper").first().boundingBox())!;
  expect(after.width - before.width).toBeGreaterThan(150);
});
