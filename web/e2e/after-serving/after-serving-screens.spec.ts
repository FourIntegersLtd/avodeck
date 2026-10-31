// S19, S20, S21, S21b and S29 acceptance: each screen at its route with axe (colour contrast included) and the
// size rules. The API is stubbed: the foundation stub plus one served case's deadlines, questions, new pages,
// the other expert's report and changes.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

const ID = (n: number) => `${n}${n}${n}${n}${n}${n}${n}${n}-${n}${n}${n}${n}-4${n}${n}${n}-8${n}${n}${n}-${String(n).repeat(12)}`;
const [V1, P1, ROUND, Q1, D1, DOC, CLAIM] = [ID(1), ID(2), ID(3), ID(4), ID(5), ID(6), ID(7)];

async function stub(page: Page) {
  await stubApi(page, { signedIn: true });
  const reply = (path: string, body: unknown, status = 200) =>
    page.route(`**/v1/cases/${CASE_A}${path}`, (route) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }));
  await reply("", { id: CASE_A, label: "Mrs A", status: "open", instructing_party_type: "lawyer", allowed_actions: [] });
  await reply("/pack", packFixtures.launch.case_pack);
  const deadline = { id: D1, kind: "written_questions", due_on: "2026-12-26", due_at: "2026-12-26T23:59:59Z", days_left: 18, timezone: "Europe/London", calendar: "calendar_days", created_from: "pack", source: "pack", rule_ref: null, pack_version: "1", hearing_id: null, done_at: null, last_reminded_on: null, version: 1 };
  await reply("/deadlines", { deadlines: [deadline], served_on: "2026-11-28" });
  await reply(`/deadlines/${D1}/changes`, { changes: [] });
  await reply("/report/served", { version: { id: V1, display_name: "Report 1", status: "served", version_no: 1, served_on: "2026-11-28", sha256: "a" }, paragraphs: [{ id: P1, para_no: 614, text: "A paragraph.", fact_ids: [] }] });
  await reply("/after-serving/steps", { meetings: [], pack: null });
  await reply("/hearings", { hearings: [], next_hearing_id: null, next_trial_hearing_id: null });
  await reply("/court-directions", { directions: [] });
  await reply("/written-question-rounds", { rounds: [{ id: ROUND, number: 1, from_party_id: null, received_on: "2026-12-01", document_id: DOC, deadline_id: D1, answers_version_id: null, status: "open", extracted_at: "2026-12-01T10:00:00Z", version: 1, open: true, due_on: "2026-12-26", days_left: 18 }], next_due_on: "2026-12-26" });
  const wq = { questions: [{ id: Q1, round_id: ROUND, number: 1, para_ref: "6.12", rule_ref: null, text_verbatim: "A question?", status: "received", suggested_classification: "clarifies", classification_reason: null, classification: null, suggested_paragraph_ids: [], paragraph_ids: [], answer_text: null, citation_refs: [], told_instructing_party_at: null, page_nos: [118], version: 1 }] };
  await reply("/written-questions", { questions: wq.questions, by_round: [{ round_id: ROUND, questions: wq.questions }] });
  await reply("/new-since-served", { pages: [{ page_id: P1, page_no: 1232, read: false, may_matter: [{ fact_id: P1, question_id: Q1, why: "a result" }] }], served_at: "2026-11-28T10:00:00Z", first_page: 1232, last_page: 1232, read_count: 0 });
  const opposingClaims = [{ id: CLAIM, claim_no: 1, text_verbatim: "A statement.", paragraph_ref: null, their_page_ref: "214", their_quote: "89", our_page_no: 214, our_quote: "98", status: "wrong_value", reason: null, checked_by: "code", discrepancy: true, expert_confirmed: false, confirmed_at: null, version: 1, needs_attention: true }];
  await reply(`/opposing-reports/${DOC}`, {
    id: "r", document_id: DOC, received_on: "2026-12-09", checked_at: "2026-12-09T10:00:00Z", claims_found: 1, expert_name: "Dr R", expert_role: "Expert", pages: 34,
    positions: [{ question_id: Q1, question_no: 2, own: [{ paragraph_id: P1, para_no: 614, text: "Our words." }], theirs: [{ paragraph_ref: "5.1", text_verbatim: "Their words." }] }],
    claims: opposingClaims,
    other_claims: opposingClaims,
    matching_claims: [], needs_attention_count: 1,
  });
  await reply("/report-changes", { changes: [], supplementary: null });
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

test("after serving passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/served`);
  await expect(page.getByRole("heading", { level: 2, name: "Your report has been served" })).toBeVisible();
  await checked(page);
});

test("questions from the other side passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/written-questions`);
  await expect(page.locator("#question-1").getByText("A question?")).toBeVisible();
  await checked(page);
});

test("new records and the other expert's report passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/new-material?document=${DOC}`);
  await expect(page.getByText("A statement.")).toBeVisible();
  await checked(page);
});

test("correct or update your report passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/correct-report`);
  await expect(page.getByRole("heading", { level: 2, name: "Correct or update your report" })).toBeVisible();
  await checked(page);
});

test("court orders and hearings passes axe, with 52px buttons and 17px body text", async ({ page }) => {
  await stub(page);
  await page.goto(`/cases/${CASE_A}/court-orders`);
  await expect(page.getByRole("heading", { level: 2, name: "Court orders and hearings" })).toBeVisible();
  await checked(page);
});
