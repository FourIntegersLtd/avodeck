// The Understand screens use the whole window on a wide screen: no empty margins beside the timeline.
import { expect, test } from "@playwright/test";
import { CASE_A, PACKS, stubUnderstand } from "./helpers";

test("11 the timeline fills a wide window", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await stubUnderstand(page, PACKS[0]!);
  await page.goto(`/cases/${CASE_A}/timeline`);
  await expect(page.getByRole("heading", { level: 2, name: "The timeline" })).toBeVisible();
  const box = (await page.locator(".av-page").first().boundingBox())!;
  expect(box.width).toBeGreaterThan(1800);
});
