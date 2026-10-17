import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubApi } from "./helpers";

test("Today shows the case card and passes axe with colour contrast", async ({ page }) => {
  await stubApi(page, { signedIn: true });
  await page.goto("/today");
  const card = page.getByRole("link", { name: /Mrs A/ });
  await expect(card).toBeVisible();
  await expect(card.getByText("Due 30 Nov")).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
});

test("buttons are at least 52px tall and body text at least 17px", async ({ page }) => {
  await stubApi(page, { signedIn: false });
  await page.goto("/sign-in");
  const button = page.getByRole("button", { name: "Sign in" });
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(52);
  const size = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
  expect(size).toBeGreaterThanOrEqual(17);
});
