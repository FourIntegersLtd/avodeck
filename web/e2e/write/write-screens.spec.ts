// S14 and S15 acceptance: both screens at their routes, with axe (colour contrast included) and the size
// rules. The API is stubbed: the foundation stub, plus the questions, points and workspace of one case.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

const Q1 = "a1111111-1111-4111-8111-111111111111";
const FACT = "f1111111-1111-4111-8111-111111111111";
const PAGE = "91111111-1111-4111-8111-111111111111";
const SIDES = [{ code: "supports_claim", label: "Side one" }, { code: "supports_clinician", label: "Side two" }];
const fact = { id: FACT, page_id: PAGE, page_no: 118, kind: "symptom", value: "Bleeding for 3 weeks", event_date: "2024-05-15" };

const HEADER = { number: 1, short_title: "A question", kind: "breach", kind_label_key: "question_type.breach", para_ref: null };

async function stub(page: Page) {
  await stubApi(page, { signedIn: true });
  // The real case pack, so the step bar shows every step of the case type.
  await page.route(`**/v1/cases/${CASE_A}/pack`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(packFixtures.launch.case_pack) }));
  const reply = (path: string, body: unknown) =>
    page.route(`**/v1/cases/${CASE_A}${path}`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }));
  await reply("", { id: CASE_A, label: "Mrs A", status: "open", instructing_party_type: null, allowed_actions: [] });
  await reply("/questions", {
    questions: [{ id: Q1, number: 1, text_verbatim: "A question.", confirmed_text: null, kind: null, outside_expertise: false, para_ref: null, status: "confirmed", version: 1, source_word_ids: [], instruction_version_id: PAGE }],
    assumptions: [], locked: true, reading: false, read_failed: false, reopened: false, rejected: 0, warnings: [], version_no: 1, instruction_version_id: PAGE,
  });
  await reply(`/questions/${Q1}/points`, {
    sides: SIDES, points: [{ id: PAGE, side: "supports_claim", status: "accepted", origin: "ai", reason: null, dismissed_reason: null, flagged: false, version: 1, fact }],
    unused: [], relevant: [], claims: [], range_note: { prompt: "Prompt from the pack", text: null, version: 0, dictated_on: null }, question: HEADER,
  });
  await reply(`/questions/${Q1}/workspace`, {
    question_id: Q1, chips: [{ fact_id: FACT, page_id: PAGE, page_no: 118, kind: "symptom", value: "Bleeding for 3 weeks", event_date: null, quote_verbatim: "bleeding" }],
    guidance_on: null, guidance: [], points: { sides: SIDES.map((s) => ({ ...s, count: 1 })), differ: 0 },
    views: [{ code: "v1", label: "View one" }], legal_test: "The pack's test.", opinion: null, question: HEADER,
  });
}

async function checked(page: Page) {
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  for (const button of await page.getByRole("button").all()) {
    if (await button.isVisible()) expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(52);
  }
  const size = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
  expect(size).toBeGreaterThanOrEqual(17);
}

test("points against passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/points?q=${Q1}`);
  await expect(page.getByRole("heading", { level: 3, name: "Side one" })).toBeVisible();
  await checked(page);
});

test("your opinion passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/opinion?q=${Q1}`);
  await expect(page.getByRole("button", { name: /Bleeding for 3 weeks/ })).toBeVisible();
  await checked(page);
});
