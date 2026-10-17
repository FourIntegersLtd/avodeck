// S26 acceptance: the close screen at its route, with axe (colour contrast included) and the
// size rules. Fixme until foundation adds closeRoutes to app/routes.tsx (the route is foundation-owned).
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";

test.fixme("the close screen passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stubApi(page, { signedIn: true });
  await page.goto(`/cases/${CASE_A}/close`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  for (const button of await page.getByRole("button").all()) {
    expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(52);
  }
  const size = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
  expect(size).toBeGreaterThanOrEqual(17);
});
