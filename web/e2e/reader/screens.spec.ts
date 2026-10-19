// Slice B's screens (09, 10) in the built app with the CSP in force, under both pack sets: axe with
// colour contrast, buttons at least 52px, body text at least 17px, and the highlight boxes on page 118.
import { expect, test } from "@playwright/test";
import { BBOXES, CASE_A, checkScreen, PACKS, stubReader } from "./helpers";

for (const { name, pack } of PACKS) {
  test.describe(`under the ${name}`, () => {
    test("09 Reading asks the expert to mark the page read before showing anything", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      await expect(page.getByRole("button", { name: "Mark as read" })).toBeVisible();
      await expect(page.getByRole("img", { name: "Page 118" })).toHaveAttribute("src", /^blob:/);
      await expect(page.getByRole("article")).toHaveCount(0);
      await checkScreen(page);
    });

    test("09 after Mark as read the suggestions appear and their highlight boxes match the cited words", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      await page.getByRole("button", { name: "Mark as read" }).click();
      await expect(page.getByRole("article", { name: /Dr P consultation/ })).toBeVisible();
      const img = await page.getByRole("img", { name: "Page 118" }).boundingBox();
      expect(img).not.toBeNull();
      const boxes = page.locator('[data-testid^="highlight-"]');
      await expect(boxes).toHaveCount(2);
      for (let i = 0; i < BBOXES.length; i++) {
        const b = (await boxes.nth(i).boundingBox())!;
        const w = BBOXES[i]!;
        expect(Math.abs(b.x - (img!.x + w.x0 * img!.width))).toBeLessThan(2);
        expect(Math.abs(b.y - (img!.y + w.y0 * img!.height))).toBeLessThan(2);
        expect(Math.abs(b.width - (w.x1 - w.x0) * img!.width)).toBeLessThan(2);
        expect(Math.abs(b.height - (w.y1 - w.y0) * img!.height)).toBeLessThan(2);
      }
      await checkScreen(page);
    });

    test("10 Pages we couldn't read lists the queue and offers one main action", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/couldnt-read`);
      await expect(page.getByRole("heading", { level: 1, name: "Pages we couldn't read" })).toBeVisible();
      await expect(page.getByText("0 of 2 done")).toBeVisible();
      await expect(page.getByRole("button", { name: "Save and next page" })).toBeVisible();
      await checkScreen(page);
    });
  });
}
