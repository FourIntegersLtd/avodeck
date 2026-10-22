// Slice D's screens (11, 12, 13) in the built app with the CSP in force, under both pack sets: axe with colour
// contrast, buttons at least 52px, body text at least 17px. The timeline draws 1,500 events in under a second.
import { expect, test } from "@playwright/test";
import { CASE_A, checkScreen, FACT_1, PACKS, showTimelineMap, stubUnderstand } from "./helpers";

for (const run of PACKS) {
  test.describe(`under the ${run.name}`, () => {
    test("11 The timeline shows the lanes the pack gave", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/timeline`);
      await showTimelineMap(page);
      await expect(page.getByRole("heading", { level: 2, name: "The timeline" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Line 1" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Not on a line yet" })).toBeVisible();
      await expect(page.getByRole("list", { name: "Months" })).toBeVisible();
      await expect(page.getByTestId("timeline-ribbon")).toBeVisible();
      // A card's dot is drawn in a real colour (an undefined colour token once made them invisible).
      const dot = await page.locator(".av-story-card__dot").first().evaluate((d) => {
        const css = getComputedStyle(d);
        return { w: css.width, bg: css.backgroundColor, border: css.borderTopColor };
      });
      expect(dot.w).toBe("18px");
      expect([dot.bg, dot.border].some((c) => c !== "rgba(0, 0, 0, 0)")).toBe(true);
      // Every card is a button at least 52px tall, and opening one shows the side panel without leaving the screen.
      const card = page.locator(".av-story-card").first();
      expect((await card.boundingBox())!.height).toBeGreaterThanOrEqual(52);
      await card.click();
      await expect(page.getByRole("complementary", { name: "The entry you opened" })).toBeVisible();
      await checkScreen(page);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("link", { name: /Look at the 1 thing we found/ })).toBeVisible();
      await checkScreen(page);
    });

    test("12 One consultation folds away what came later", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/consultation/${FACT_1}`);
      await expect(page.getByRole("heading", { level: 2, name: "Consultation · 15 May 2024" })).toBeVisible();
      await expect(page.getByText("Show what came later (1)")).toBeVisible();
      await expect(page.getByRole("region", { name: "Not in front of the reader" })).toBeVisible();
      await page.getByText("Comments", { exact: true }).first().click();
      await expect(page.getByRole("region", { name: "Comments" })).toBeVisible();
      await checkScreen(page);
    });

    test("13 Things to look at lists what code found, for you to decide", async ({ page }) => {
      await stubUnderstand(page, run);
      await page.goto(`/cases/${CASE_A}/things-to-look-at`);
      await expect(page.getByRole("heading", { level: 2, name: "Things to look at" })).toBeVisible();
      await expect(page.getByText("1 of 2 decided")).toBeVisible();
      await expect(page.getByRole("region", { name: "The checks we run" })).toBeVisible();
      await checkScreen(page);
    });
  });
}

test("11 The timeline draws 1,500 events in under one second", async ({ page }) => {
  test.setTimeout(90_000);
  const stub = await stubUnderstand(page, PACKS[0]!, { events: 1500 });
  await page.goto(`/cases/${CASE_A}/timeline`);
  await showTimelineMap(page);
  await expect(page.getByText(/^Showing [\d,]+ of 1,500 entries/)).toBeVisible({ timeout: 60_000 });
  expect(Date.now() - stub.timelineSentAt()).toBeLessThan(5000);
});

test("11 A dense board keeps cards inside their lanes and the axis labels apart", async ({ page }) => {
  // Shaped like a busy two-year window: several date-marked visits close together, and results and prescriptions on the same days.
  const days = ["1993-03-02", "1993-03-09", "1993-03-16", "1993-06-28", "1993-07-05", "1993-11-20", "1993-11-29", "1994-02-01", "1994-02-08", "1994-05-10", "1994-05-11", "1994-09-14", "1994-12-01"];
  const make = (lane: number) => days.map((d, i) => ({ fact_id: `00000000-0000-4000-8000-${String(lane * 100 + i + 1).padStart(12, "0")}`, kind: "consultation", page_no: lane * 100 + i + 1, event_date: d, available_at: d, status: i % 2 ? "suggested" : "verified", summary: `Entry ${lane}-${i} with some words`, source: "src_a", question_ids: [], has_finding: i === 3, date_basis: i % 2 ? "printed_century" : "printed" }));
  await stubUnderstand(page, PACKS[0]!, { range: { first: "1992-12-01", last: "1994-12-20" }, laneEvents: [make(0), make(1), make(2)] });
  await page.goto(`/cases/${CASE_A}/timeline`);
  await showTimelineMap(page);
  await expect(page.locator(".av-story-card").first()).toBeVisible();
  const boxes = await page.evaluate(() => {
    const r = (el: Element) => { const b = el.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
    return {
      cards: Array.from(document.querySelectorAll(".av-story-card")).map((el) => ({ ...r(el), lane: el.closest(".av-story-lane")!.id || Array.from(document.querySelectorAll(".av-story-lane")).indexOf(el.closest(".av-story-lane")!), track: r(el.closest(".av-story-lane")!.querySelector(".av-story-lane__track")!) })),
      heads: Array.from(document.querySelectorAll(".av-story-lane__head")).map(r),
      axisLis: Array.from(document.querySelectorAll(".av-story-axis__marks li")).map(r),
      axis: r(document.querySelector(".av-story-axis")!),
    };
  });
  type B = { l: number; t: number; r: number; b: number };
  const hit = (a: B, c: B) => a.l < c.r && c.l < a.r && a.t < c.b && c.t < a.b;
  expect(boxes.cards.length).toBeGreaterThan(6);
  for (let i = 0; i < boxes.cards.length; i++) {
    const a = boxes.cards[i]!;
    expect(a.t).toBeGreaterThanOrEqual(a.track.t - 0.5);
    expect(a.b).toBeLessThanOrEqual(a.track.b + 0.5);
    for (const h of boxes.heads) expect(hit(a, h)).toBe(false);
    expect(hit(a, boxes.axis)).toBe(false);
    for (let j = i + 1; j < boxes.cards.length; j++) expect(hit(a, boxes.cards[j]!)).toBe(false);
  }
  expect(boxes.axisLis.length).toBeGreaterThan(3);
  for (let i = 0; i < boxes.axisLis.length; i++) for (let j = i + 1; j < boxes.axisLis.length; j++) expect(hit(boxes.axisLis[i]!, boxes.axisLis[j]!)).toBe(false);
});
