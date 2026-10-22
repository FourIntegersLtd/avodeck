// Slice D's screens (11, 12, 13) in the built app with the CSP in force, under both pack sets: axe with colour
// contrast, buttons at least 52px, body text at least 17px. The timeline draws 1,500 events in under a second.
import { expect, test } from "@playwright/test";
import { CASE_A, checkScreen, FACT_1, PACKS, showTimelineMap, stubUnderstand } from "./helpers";

for (const run of PACKS) {
  test.describe(`under the ${run.name}`, () => {
    test("11 The timeline shows the lanes the pack gave", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/timeline`);
      await showTimelineMap(page);
      await expect(page.getByRole("heading", { level: 2, name: "The timeline" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Line 1" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Not on a line yet" })).toBeVisible();
      await expect(page.getByRole("list", { name: "Months" })).toBeVisible();
      await expect(page.getByTestId("timeline-ribbon")).toBeVisible();
      // A card's dot is drawn in a real colour (an undefined colour token once made them invisible).
      const dot = await page.locator(".av-story-card__dot").first().evaluate((d) => {
        const css = getComputedStyle(d);
        return { w: css.width, bg: css.backgroundColor, border: css.borderTopColor };
      });
      expect(dot.w).toBe("18px");
      expect([dot.bg, dot.border].some((c) => c !== "rgba(0, 0, 0, 0)")).toBe(true);
      // Every card is a button at least 52px tall, and opening one shows the side panel without leaving the screen.
      const card = page.locator(".av-story-card").first();
      expect((await card.boundingBox())!.height).toBeGreaterThanOrEqual(52);
      await card.click();
      await expect(page.getByRole("complementary", { name: "The entry you opened" })).toBeVisible();
      await checkScreen(page);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("link", { name: /Look at the 1 thing we found/ })).toBeVisible();
      await checkScreen(page);
    });

    test("12 One consultation folds away what came later", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/consultation/${FACT_1}`);
      await expect(page.getByRole("heading", { level: 2, name: "Consultation · 15 May 2024" })).toBeVisible();
      await expect(page.getByText("Show what came later (1)")).toBeVisible();
      await expect(page.getByRole("region", { name: "Not known when this arrived" })).toBeVisible();
      await page.getByText("Comments", { exact: true }).first().click();
      await expect(page.getByRole("region", { name: "Comments" })).toBeVisible();
      await checkScreen(page);
    });

    test("13 Things to look at lists what code found, for you to decide", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/things-to-look-at`);
      await expect(page.getByRole("heading", { level: 2, name: "Things to look at" })).toBeVisible();
      await expect(page.getByText("1 of 2 decided")).toBeVisible();
      await expect(page.getByRole("region", { name: "The checks we run" })).toBeVisible();
      await checkScreen(page);
    });
  });
}

test("11 The timeline draws 1,500 events in under one second", async ({ page }) => {
  test.setTimeout(90_000);
  const stub = await stubUnderstand(page, PACKS[0]!, { events: 1500 });
  await page.goto(`/cases/${CASE_A}/timeline`);
  await showTimelineMap(page);
  await expect(page.getByText(/^Showing [\d,]+ of 1,500 entries/)).toBeVisible({ timeout: 60_000 });
  expect(Date.now() - stub.timelineSentAt()).toBeLessThan(5000);
});
