// S02, S04, S05 and S06 in the built app (CSP in force): each shows its main content, passes axe with colour
// contrast, keeps every button at least 52px tall and body text at least 17px.
import { expect, test } from "@playwright/test";
import { CASE_A, expectAccessible, stubSetupApi } from "./helpers";

test.beforeEach(async ({ page }) => {
  await stubSetupApi(page);
});

test("S02 Your expert profile", async ({ page }) => {
  await page.goto("/profile");
  await expect(page.getByRole("heading", { level: 1, name: "Your expert profile" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Where you've practised" }).getByText("2011 – now")).toBeVisible();
  await expect(page.getByRole("region", { name: "Your report template" }).getByLabel("My opinion")).toBeVisible();
  await expect(page.getByRole("region", { name: "Your balance over the last 12 months" })).toBeVisible();
  await expectAccessible(page);
});

test("S04 Start a new case", async ({ page }) => {
  await page.goto("/cases/new");
  await expect(page.getByRole("heading", { level: 1, name: "Start a new case" })).toBeVisible();
  await expect(page.getByRole("radiogroup", { name: /Who is this case for\?/ })).toBeVisible();
  await expect(page.getByRole("radiogroup", { name: "Which side are you instructed by?" })).toBeVisible();
  await expectAccessible(page);
});

test("S04 Set up the case", async ({ page }) => {
  await page.goto(`/cases/${CASE_A}/details`);
  await expect(page.getByRole("heading", { level: 2, name: "Set up the case" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Found in the letter" }).getByRole("button", { name: "Use this" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Who instructed you?" }).getByText("Ms Rees, Rees & Co")).toBeVisible();
  await expectAccessible(page);
});

test("S05 A quick check before you start", async ({ page }) => {
  await page.goto(`/cases/${CASE_A}/conflicts`);
  await expect(page.getByRole("heading", { level: 2, name: "A quick check before you start" })).toBeVisible();
  await expect(page.getByRole("region", { name: "What we'll add to your report" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm and continue" })).toBeVisible();
  await expectAccessible(page);
});

test("S05 stepping back opens a dialog that passes axe", async ({ page }) => {
  await page.goto(`/cases/${CASE_A}/conflicts`);
  await page.getByRole("button", { name: "Step back from this case" }).click();
  await expect(page.getByRole("dialog", { name: "Step back from this case" })).toBeVisible();
  await expectAccessible(page);
});

test("S06 What you've been asked", async ({ page }) => {
  await page.goto(`/cases/${CASE_A}/questions`);
  await expect(page.getByRole("heading", { level: 2, name: "What you've been asked" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Question 2" }).getByText("The letter says:")).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft a note about this" })).toBeVisible();
  await expect(page.getByText("1 of 3 confirmed")).toBeVisible();
  await expectAccessible(page);
});
