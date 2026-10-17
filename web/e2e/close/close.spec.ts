// S26 acceptance: the close screen at its route, with axe (colour contrast included) and the
// size rules. The API is stubbed: the foundation stub, plus a settled case's close summary.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";

const CASE = {
  id: CASE_A,
  label: "Mrs A",
  status: "settled",
  instructing_party_type: null,
  allowed_actions: ["case.view_metadata", "case.export", "case.retention", "case.delete_now", "case.archive", "case.reopen", "case.view_certificate", "legal_hold.set", "legal_hold.release"],
};
const SUMMARY = {
  case_id: CASE_A,
  status: "settled",
  settled_at: "2026-10-01T09:00:00Z",
  ever_served: true,
  retention_policy: null,
  retention_years: 6,
  delete_after: null,
  held: false,
  holds: [],
  deletion_started: false,
  deleted_at: null,
};

test("the close screen passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stubApi(page, { signedIn: true });
  await page.route(`**/v1/cases/${CASE_A}`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASE) }),
  );
  await page.route(`**/v1/cases/${CASE_A}/close`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SUMMARY) }),
  );
  await page.goto(`/cases/${CASE_A}/close`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("button").first()).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  for (const button of await page.getByRole("button").all()) {
    expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(52);
  }
  const size = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
  expect(size).toBeGreaterThanOrEqual(17);
});
