import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubApi } from "./helpers";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test("Sign in passes axe with colour contrast", async ({ page }) => {
  await stubApi(page, { signedIn: false });
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(axe.violations).toEqual([]);
});

test("Account and security passes axe with colour contrast", async ({ page }) => {
  await stubApi(page, { signedIn: true });
  await page.goto("/account");
  await expect(page.getByRole("button", { name: "Make new codes" })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(axe.violations).toEqual([]);
});
