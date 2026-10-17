import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubApi } from "./helpers";

test("sign in with password and code, then Today", async ({ page }) => {
  await stubApi(page, { signedIn: false });
  await page.goto("/today");
  await expect(page.getByRole("heading", { level: 1, name: "Welcome back" })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  await page.getByLabel("Email address").fill("j.morgan@example.test");
  await page.getByLabel("Password", { exact: true }).fill("a long enough password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("6-digit code").fill("123456");
  await page.getByRole("button", { name: "Carry on" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  const stored = await page.evaluate(() => localStorage.length + sessionStorage.length);
  expect(stored).toBe(0);
});
