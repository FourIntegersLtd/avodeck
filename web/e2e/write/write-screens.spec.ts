// S14 and S15 acceptance: both screens at their routes, with axe (colour contrast included) and the size
// rules. The API is stubbed: the foundation stub, plus the questions, points and workspace of one case.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

const Q1 = "a1111111-1111-4111-8111-111111111111";
const Q2 = "a2222222-2222-4222-8222-222222222222";
const Q3 = "a3333333-3333-4333-8333-333333333333";
const Q4 = "a4444444-4444-4444-8444-444444444444";
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
  const ids = [Q1, Q2, Q3, Q4];
  await reply("/questions", {
    questions: ids.map((id, i) => ({ id, number: i + 1, text_verbatim: `Question ${i + 1} text.`, confirmed_text: null, kind: null, outside_expertise: false, para_ref: null, status: "confirmed", version: 1, source_word_ids: [], instruction_version_id: PAGE })),
    assumptions: [], locked: true, reading: false, read_failed: false, reopened: false, rejected: 0, warnings: [], version_no: 1, instruction_version_id: PAGE,
  });
  await reply("/write-progress", {
    answered: 2, total: 4,
    questions: ids.map((id, i) => ({ question_id: id, number: i + 1, short_title: ["First visit", "Second visit", "Referral timing", "Diagnosis timing"][i], points_done: i < 2, has_opinion: i < 2 })),
  });
  const header = { ...HEADER, number: 2, para_ref: "4.2" };
  const point = (n: number, side: string, value: string, page_no: number) => ({
    id: `b${n}111111-1111-4111-8111-111111111111`, side, status: "accepted", origin: "ai", reason: null, dismissed_reason: null, flagged: false, version: 1,
    fact: { ...fact, id: `f${n}111111-1111-4111-8111-111111111111`, value, page_no },
  });
  await reply(`/questions/${Q2}/points`, {
    sides: SIDES,
    points: [
      point(2, "supports_claim", "Bleeding for 3 weeks in a 58-year-old", 118), point(3, "supports_claim", "Iron deficiency anaemia already on file, cause unexplained", 214),
      point(4, "supports_claim", "No abdominal or rectal examination recorded", 118), point(5, "supports_claim", "No safety-netting recorded", 118),
      point(6, "supports_clinician", "The patient offered the explanation herself", 118), point(7, "supports_clinician", "Bleeding described as on the paper only", 118),
      point(8, "supports_clinician", "Review if persists written in the plan", 118),
    ],
    unused: [{ ...fact, id: "f9111111-1111-4111-8111-111111111111", value: "Out-of-hours call mentions tummy pain", page_no: 301 }],
    relevant: [{ id: "d1111111-1111-4111-8111-111111111111", status: "suggested", version: 1, why_relevant: "The out-of-hours call mentions tummy pain, but never reached the clinician. You may want to say whether it matters.", fact: { ...fact, id: "f9222222-2222-4222-8222-222222222222", value: "Out-of-hours call on 22 April", page_no: 301 } }],
    claims: [{ id: "c1111111-1111-4111-8111-111111111111", claim_index: 1, document_id: PAGE, expert_verdict: null, verdict: "contradicts", status: "suggested", version: 1, word_ids: [], text_verbatim: "She told the clinician she had lost weight.", facts: [{ ...fact, id: "f9333333-3333-4333-8333-333333333333", value: "Weight loss is not mentioned until August", page_no: 412 }] }],
    range_note: { prompt: "What others might reasonably do", text: "Some would treat fresh bleeding with a clear local cause conservatively at first.", version: 1, dictated_on: "2026-10-14" }, question: header,
  });
  await reply(`/questions/${Q2}/workspace`, {
    question_id: Q2, chips: [{ fact_id: FACT, page_id: PAGE, page_no: 118, kind: "symptom", value: "Bleeding for 3 weeks", event_date: null, quote_verbatim: "bleeding" }],
    guidance_on: null, guidance: [], points: { sides: SIDES.map((s) => ({ ...s, count: 4 })), differ: 1 },
    views: [{ code: "v1", label: "View one" }, { code: "v2", label: "View two" }, { code: "v3", label: "View three" }], legal_test: "The pack's test of what a reasonable practitioner would have done.", opinion: null, question: header,
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
  await page.goto(`/cases/${CASE_A}/points?q=${Q2}`);
  await expect(page.getByRole("heading", { level: 3, name: "Side one" })).toBeVisible();
  await checked(page);
});

test("your opinion passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/opinion?q=${Q2}`);
  await expect(page.getByRole("button", { name: /Bleeding for 3 weeks/ })).toBeVisible();
  await checked(page);
});
