// Screen 09's page viewer in the built app (approved 2026-10-23, items 1 to 4). The proof that matters:
// after every turn (90, 180, 270) and every undo, each highlight and each word in the word picker still
// sits over the same words of the page (rule 2), and the picture itself fills the turned box.
import { expect, test, type Locator, type Page } from "@playwright/test";
import { BBOXES, CASE_A, checkScreen, PACKS, stubReader, WORDS } from "./helpers";

type Box = { x0: number; y0: number; x1: number; y1: number };

/** Where a box on the upright page lands once the page is turned clockwise (fractions of the turned page). */
function turnedBox(b: Box, turn: number): Box {
  if (turn === 90) return { x0: 1 - b.y1, y0: b.x0, x1: 1 - b.y0, y1: b.x1 };
  if (turn === 180) return { x0: 1 - b.x1, y0: 1 - b.y1, x1: 1 - b.x0, y1: 1 - b.y0 };
  if (turn === 270) return { x0: b.y0, y0: 1 - b.x1, x1: b.y1, y1: 1 - b.x0 };
  return b;
}

async function expectOver(el: Locator, frame: { x: number; y: number; width: number; height: number }, b: Box) {
  const r = (await el.boundingBox())!;
  expect(Math.abs(r.x - (frame.x + b.x0 * frame.width))).toBeLessThan(2);
  expect(Math.abs(r.y - (frame.y + b.y0 * frame.height))).toBeLessThan(2);
  expect(Math.abs(r.width - (b.x1 - b.x0) * frame.width)).toBeLessThan(2);
  expect(Math.abs(r.height - (b.y1 - b.y0) * frame.height)).toBeLessThan(2);
}

async function expectAligned(page: Page, turn: number) {
  const turned = page.getByTestId("page-turn");
  await expect(turned).toHaveAttribute("data-turn", String(turn));
  const frame = (await turned.boundingBox())!;
  // A portrait page (1000 x 1400) turned a quarter is landscape.
  const ratio = frame.height / frame.width;
  expect(Math.abs(ratio - (turn % 180 === 0 ? 1.4 : 1 / 1.4))).toBeLessThan(0.02);
  const img = (await page.getByRole("img", { name: "Page 118" }).boundingBox())!;
  for (const k of ["x", "y", "width", "height"] as const) expect(Math.abs(img[k] - frame[k])).toBeLessThan(2);
  const boxes = page.locator('[data-testid^="highlight-"]');
  await expect(boxes).toHaveCount(2);
  for (let i = 0; i < BBOXES.length; i++) await expectOver(boxes.nth(i), frame, turnedBox(BBOXES[i]!, turn));
}

/** Tools other than Turn page, zoom and Find words sit in the More tools panel, which sits beside the "Page view" bar and closes after a pick. */
async function moreTool(page: Page, name: string) {
  const more = page.getByRole("button", { name: "More tools" });
  if ((await more.getAttribute("aria-expanded")) !== "true") await more.click();
  await page.getByRole("group", { name: "More tools" }).getByRole("button", { name, exact: true }).click();
}

async function openRead(page: Page) {
  await page.goto(`/cases/${CASE_A}/read/118`);
  await page.getByRole("button", { name: "Mark as read" }).click();
  await expect(page.getByRole("article", { name: /Dr P consultation/ })).toBeVisible();
}

for (const { name, pack } of PACKS) {
  test.describe(`the page viewer under the ${name}`, () => {
    test("09 turning keeps every highlight over its words at 90, 180, 270 and after each undo", async ({ page }) => {
      const seen = await stubReader(page, pack);
      await openRead(page);
      const bar = page.getByRole("toolbar", { name: "Page view" });
      await expectAligned(page, 0);
      for (const turn of [90, 180, 270]) {
        await bar.getByRole("button", { name: "Turn page" }).click();
        await expectAligned(page, turn);
      }
      for (const turn of [180, 90, 0]) {
        await moreTool(page, "Undo turn");
        await expectAligned(page, turn);
      }
      await moreTool(page, "Turn left");
      await expectAligned(page, 270);
      await page.keyboard.press("t");
      await expectAligned(page, 0);
      await moreTool(page, "Upside down");
      await expectAligned(page, 180);
      await expect.poll(() => seen.turns).toEqual([90, 180, 270, 180, 90, 0, 270, 0, 180]);
    });

    test("09 the word picker's words sit over the page's words on a turned page", async ({ page }) => {
      await stubReader(page, pack);
      await openRead(page);
      await page.getByRole("toolbar", { name: "Page view" }).getByRole("button", { name: "Turn page" }).click();
      await page.getByRole("button", { name: "Change a detail" }).click();
      const frame = (await page.getByTestId("page-turn").boundingBox())!;
      for (const word of WORDS) await expectOver(page.getByRole("button", { name: word.text, exact: true }), frame, turnedBox(word.box!, 90));
    });

    test("09 the turn is kept: the page opens turned the next time", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      await moreTool(page, "Turn left");
      await expect(page.getByTestId("page-turn")).toHaveAttribute("data-turn", "270");
      await page.reload();
      await expect(page.getByTestId("page-turn")).toHaveAttribute("data-turn", "270");
    });

    test("09 the page area keeps two-finger and one-finger gestures for itself, so pinch zooms the page, not the screen", async ({ page }) => {
      await stubReader(page, pack);
      await openRead(page);
      // Without this a touch screen takes the pinch for the whole screen and cancels the page's own zoom and drag.
      expect(await page.locator(".rd-paper").evaluate((el) => getComputedStyle(el).touchAction)).toBe("none");
    });

    test("09 Whole page fits the page in view; Ctrl and the wheel zoom; the zoom stays on the next page", async ({ page }) => {
      await stubReader(page, pack);
      await openRead(page);
      const bar = page.getByRole("toolbar", { name: "Page view" });
      await moreTool(page, "Whole page");
      const paper = (await page.locator(".rd-paper").boundingBox())!;
      const fitted = (await page.getByTestId("page-turn").boundingBox())!;
      expect(fitted.height).toBeLessThanOrEqual(paper.height + 1);
      expect(fitted.width).toBeLessThanOrEqual(paper.width + 1);

      await moreTool(page, "Fit width");
      await page.locator(".rd-paper").hover();
      await page.keyboard.down("Control");
      await page.mouse.wheel(0, -100);
      await page.keyboard.up("Control");
      await expect(bar.getByText("110%")).toBeVisible();
      await page.getByRole("button", { name: "Next page" }).click();
      await expect(page.getByRole("img", { name: "Page 119" })).toBeVisible();
      await expect(page.getByRole("toolbar", { name: "Page view" }).getByText("110%")).toBeVisible();
    });

    test("09 the page strip and Go to page, with Make it clearer open, pass axe and the size checks", async ({ page }) => {
      await stubReader(page, pack);
      await page.goto(`/cases/${CASE_A}/read/118`);
      const strip = page.getByRole("listbox", { name: "Pages" });
      await expect(strip.getByRole("option", { name: "Page 118, not read yet" })).toHaveAttribute("aria-selected", "true");
      await expect(strip.getByRole("option", { name: "Page 117, read" })).toBeVisible();
      expect(await strip.getByRole("option").count()).toBeLessThan(60);
      await moreTool(page, "Make it clearer");
      await expect(page.getByRole("slider", { name: "Brightness" })).toBeVisible();
      await checkScreen(page);

      await strip.focus();
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      await expect(page.getByRole("img", { name: "Page 1231" })).toBeVisible();
      await page.getByRole("textbox", { name: "Go to page" }).fill("40");
      await page.keyboard.press("Enter");
      await expect(page.getByRole("img", { name: "Page 40" })).toBeVisible();
    });
  });
}
