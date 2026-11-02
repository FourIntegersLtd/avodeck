// Every signed-in screen fits a phone (390 px wide): the page never scrolls sideways.
// Read full-screen scrolls to its bottom, and the timeline's own scroller takes the wide board.
import { expect, test } from "@playwright/test";
import { PACKS, stubReader } from "../reader/helpers";
import { PACKS as UNDERSTAND_PACKS, stubUnderstand } from "../understand/helpers";
import { CASE_A, stubApi } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

const ROUTES = [
  "/today",
  "/cases",
  "/help",
  "/account",
  "/messages",
  "/profile",
  "/cases/new",
  "/organisations/new",
  "/account/custodian",
  "/account/close",
  "/support",
  `/cases/${CASE_A}/home`,
  `/cases/${CASE_A}/papers/add`,
  `/cases/${CASE_A}/preparing`,
  `/cases/${CASE_A}/bundle`,
  `/cases/${CASE_A}/bundle/copies`,
  `/cases/${CASE_A}/bundle/ask`,
  `/cases/${CASE_A}/bundle/not-this-patient`,
  `/cases/${CASE_A}/read`,
  `/cases/${CASE_A}/read/118`,
  `/cases/${CASE_A}/read/transcript`,
  `/cases/${CASE_A}/read/couldnt-read`,
  `/cases/${CASE_A}/timeline`,
  `/cases/${CASE_A}/things-to-look-at`,
  `/cases/${CASE_A}/consultation/88888888-8888-4888-8888-000000000001`,
  `/cases/${CASE_A}/points`,
  `/cases/${CASE_A}/opinion`,
  `/cases/${CASE_A}/report`,
  `/cases/${CASE_A}/final-check`,
  `/cases/${CASE_A}/sign`,
  `/cases/${CASE_A}/invoice`,
  `/cases/${CASE_A}/served`,
  `/cases/${CASE_A}/written-questions`,
  `/cases/${CASE_A}/new-material`,
  `/cases/${CASE_A}/correct-report`,
  `/cases/${CASE_A}/court-orders`,
  `/cases/${CASE_A}/trial-pages`,
  `/cases/${CASE_A}/trial-pack`,
  `/cases/${CASE_A}/joint-statement`,
  `/cases/${CASE_A}/close`,
  `/cases/${CASE_A}/team`,
  `/cases/${CASE_A}/questions`,
  `/cases/${CASE_A}/details`,
  `/cases/${CASE_A}/conflicts`,
  `/cases/${CASE_A}/agree`,
];

for (const path of ROUTES) {
  test(`no sideways scroll on a phone: ${path}`, async ({ page }) => {
    await stubApi(page, { signedIn: true });
    await stubReader(page, PACKS[0]!.pack);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(fits, `${path} scrolls sideways`).toBe(true);
  });
}

test("Read full-screen scrolls to the bottom on a phone", async ({ page }) => {
  await stubReader(page, PACKS[0]!.pack);
  await page.route("**/v1/cases/*/my-notes", (r) => r.fulfill({ json: { notes: [] } }));
  await page.goto(`/cases/${CASE_A}/read/118`);
  await page.getByRole("button", { name: "I've read this page" }).click();
  await page.getByRole("button", { name: "Full screen" }).click();
  const full = page.locator(".rd.rd--full");
  await expect(full).toBeVisible();
  await full.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(page.getByRole("checkbox", { name: "Bookmark this page" })).toBeInViewport();
});

test("the timeline scrolls sideways inside its own scroller, not the page", async ({ page }) => {
  await stubUnderstand(page, UNDERSTAND_PACKS[0]!);
  await page.goto(`/cases/${CASE_A}/timeline`);
  const scroller = page.locator(".av-story-scroll").first();
  await expect(scroller).toBeVisible();
  const wide = await scroller.evaluate((el) => el.scrollWidth > el.clientWidth);
  expect(wide).toBe(true);
  const pageFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(pageFits).toBe(true);
});
