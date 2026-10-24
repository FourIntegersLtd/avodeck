// Screen 09, the calm redesign in the built app: one slim find bar, and one primary button before the read.
import { expect, test } from "@playwright/test";
import { CASE_A, checkScreen, PACKS, stubReader } from "./helpers";

for (const { name, pack } of PACKS) {
  test.describe(`the calm reader under the ${name}`, () => {
    test("09 the find bar is one slim row, and passes axe and the size checks", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      await page.getByRole("toolbar", { name: "Page view" }).getByRole("button", { name: "Find", exact: true }).click();
      const bar = page.getByRole("search", { name: "Find words in the bundle" });
      await bar.getByRole("searchbox").fill("chest");
      await expect(bar.getByText("Page 1 of 2")).toBeVisible();
      const row = (await bar.boundingBox())!;
      expect(row.height).toBeLessThan(90);
      const tops = await bar.locator("input, button").evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top / 10)));
      expect(new Set(tops).size).toBeLessThanOrEqual(2);
      await checkScreen(page);
    });

    test("09 only one primary button is on screen before the read, and the screen passes axe", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      const primary = page.locator("button.av-button:not(.av-button--secondary):not(.av-button--quiet)");
      await expect(page.getByRole("button", { name: "I've read this page" })).toBeVisible();
      await expect(primary).toHaveCount(1);
      await checkScreen(page);
      await page.getByRole("button", { name: "I've read this page" }).click();
      await expect(page.getByRole("button", { name: "Correct" })).toBeVisible();
      await checkScreen(page);
    });
  });
}
