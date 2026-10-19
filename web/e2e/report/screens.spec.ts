// Slice F's screens (16, 17, 18, 18b) in the built app with the CSP in force, under both pack sets:
// axe with colour contrast, buttons at least 52px, body text at least 17px.
import { expect, test } from "@playwright/test";
import { CASE_A, checkScreen, PACKS, stubReport } from "./helpers";

for (const { name, pack } of PACKS) {
  test.describe(`under the ${name}`, () => {
    test("16 Your report shows the sections, the paper page and what the court requires", async ({ page }) => {
      await stubReport(page, pack);
      await page.goto(`/cases/${CASE_A}/report`);
      await expect(page.getByRole("heading", { level: 1, name: "Your report" })).toBeVisible();
      await expect(page.getByRole("region", { name: "The report page" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Run the final check" })).toBeVisible();
      await checkScreen(page);
    });

    test("17 Final check with two things to fix", async ({ page }) => {
      await stubReport(page, pack, { check: "failed" });
      await page.goto(`/cases/${CASE_A}/final-check`);
      await expect(page.getByRole("heading", { level: 1, name: "Final check: 1 thing to fix" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Fix it" })).toBeVisible();
      await checkScreen(page);
    });

    test("17 Final check passed", async ({ page }) => {
      await stubReport(page, pack, { check: "passed" });
      await page.goto(`/cases/${CASE_A}/final-check`);
      await expect(page.getByRole("heading", { level: 1, name: "Final check: all clear" })).toBeVisible();
      await checkScreen(page);
    });

    test("18 Ready to sign", async ({ page }) => {
      await stubReport(page, pack, { check: "passed" });
      await page.goto(`/cases/${CASE_A}/sign`);
      await expect(page.getByRole("heading", { level: 1, name: "Ready to sign" })).toBeVisible();
      await expect(page.getByRole("button", { name: /^Sign and send to the / })).toBeDisabled();
      await page.getByRole("checkbox").check();
      await expect(page.getByRole("button", { name: /^Sign and send to the / })).toBeEnabled();
      await checkScreen(page);
    });

    test("18b Report sent, here is your invoice", async ({ page }) => {
      await stubReport(page, pack, { status: "served", check: "passed", invoice: "sent" });
      await page.goto(`/cases/${CASE_A}/invoice`);
      await expect(page.getByRole("heading", { level: 1, name: "Report sent. Here's your invoice." })).toBeVisible();
      await expect(page.getByRole("article", { name: "Invoice JM-2026-031" })).toBeVisible();
      await checkScreen(page);
    });

    test("18b with no invoice made yet", async ({ page }) => {
      await stubReport(page, pack, { status: "served", check: "passed", invoice: "none" });
      await page.goto(`/cases/${CASE_A}/invoice`);
      await expect(page.getByRole("button", { name: "Make the invoice" })).toBeVisible();
      await checkScreen(page);
    });
  });
}
