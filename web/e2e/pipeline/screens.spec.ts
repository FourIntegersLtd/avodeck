// Slice A's screens (07, 08, 08b, 30) in the built app with the CSP in force, under both pack sets:
// axe with colour contrast, buttons at least 52px, body text at least 17px.
import { expect, test } from "@playwright/test";
import { askLabel, CASE_A, checkScreen, PACKS, stubPipeline } from "./helpers";

for (const { name, pack } of PACKS) {
  test.describe(`under the ${name}`, () => {
    test("07 Preparing your bundle follows the job's progress", async ({ page }) => {
      await stubPipeline(page, pack);
      await page.goto(`/cases/${CASE_A}/preparing`);
      await expect(page.getByRole("heading", { level: 1, name: "Preparing your bundle" })).toBeVisible();
      await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "48");
      await checkScreen(page);
    });

    test("08 Your bundle is ready shows the summary and one main action", async ({ page }) => {
      await stubPipeline(page, pack);
      await page.goto(`/cases/${CASE_A}/bundle`);
      await expect(page.getByRole("heading", { level: 1, name: "Your bundle is ready" })).toBeVisible();
      await expect(page.getByRole("link", { name: askLabel(pack) })).toBeVisible();
      await expect(page.getByRole("link", { name: "Start reading" })).toBeVisible();
      await checkScreen(page);
    });

    test("08 Copies set aside shows each copy with its pictures", async ({ page }) => {
      await stubPipeline(page, pack);
      await page.goto(`/cases/${CASE_A}/bundle/copies`);
      await expect(page.getByText("p.20 is a copy of p.3")).toBeVisible();
      await expect(page.getByRole("img", { name: "Page 20" })).toHaveAttribute("src", /^data:image\/jpeg/);
      await checkScreen(page);
    });

    test("08b asks the instructing party in the pack's words", async ({ page }) => {
      await stubPipeline(page, pack);
      await page.goto(`/cases/${CASE_A}/bundle/ask`);
      await expect(page.getByRole("heading", { level: 1, name: `${askLabel(pack)} for what's missing` })).toBeVisible();
      await page.getByRole("button", { name: "Edit the email" }).click();
      await expect(page.getByLabel("Subject")).toHaveValue("Records needed - Mrs A");
      await checkScreen(page);
    });

    test("30 Not this patient's record lets the expert decide each page", async ({ page }) => {
      await stubPipeline(page, pack);
      await page.goto(`/cases/${CASE_A}/bundle/not-this-patient`);
      await expect(page.getByRole("heading", { level: 1, name: "These pages may belong to someone else" })).toBeVisible();
      await page.getByRole("button", { name: "Not Mrs A's" }).first().click();
      await expect(page.getByRole("button", { name: "Set aside 1 page and draft the note" })).toBeEnabled();
      await checkScreen(page);
    });
  });
}
