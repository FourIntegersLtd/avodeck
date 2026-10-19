// S22-S25 acceptance: each trial screen at its route, with axe (colour contrast included) and the size
// rules. The API is stubbed: the foundation stub, plus the trial endpoints. The witness-box screen gets no
// API at all (it works from the copy on the device).
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";

const ACTIONS = ["case.view_metadata", "case.view_content", "case.edit_setup", "case.upload", "opinion.write", "fact.verify", "report.build", "report.sign", "request.draft", "case.export"];
const CASE = { id: CASE_A, label: "Mrs A", status: "served", instructing_party_type: null, allowed_actions: ACTIONS };
const PACK = {
  workflow_steps: [{ key: "trial", screens: ["expert_meeting", "court_bundle_pages", "trial_pack", "witness_box"] }],
  party_roles: [], instructing_party_types: [], fact_kinds: [], locale: "en-GB", timezone: "Europe/London", date_order: "DMY",
  vocabulary: { court_bundle: "Zz binder", expert_meeting: "Zz meeting", expert_meeting_document: "Zz minute" },
};
const MEETING = {
  id: "a9111111-1111-4111-8111-111111111111", type: "zz_conference", label: "Zz minute", status: "draft", hearing_id: null,
  discussion_on: "2027-02-12", sign_by: "2027-02-19", sign_rule_ref: "Rule Z.9", send_by: "2027-02-26", send_rule_ref: "Rule Z.10",
  declarations: ["Zz we affirm our duty."], prepared_sha256: null, prepared_pages: null, certificate_sha256: null, signed_sha256: null, version: 1,
  items: [{ id: "a9222222-2222-4222-8222-222222222221", number: 1, text: "Zz question one?", own_position: "Zz yes.", other_positions: "Zz yes.", agreed: true, reason: null, change_note: null, deviation: "consistent", deviation_reason: "same_words", served_paragraphs: [1], changed_from_previous: "same", version: 1 }],
  agenda_versions: [{ id: "a9777777-7777-4777-8777-777777777777", version_no: 1, read_at: "2027-02-10T09:00:00Z", item_count: 1, created_at: "2027-02-10T09:00:00Z" }],
};
const BODIES: Record<string, unknown> = {
  [`/v1/cases/${CASE_A}`]: CASE,
  [`/v1/cases/${CASE_A}/pack`]: PACK,
  [`/v1/cases/${CASE_A}/expert-meetings`]: { meetings: [MEETING] },
  [`/v1/cases/${CASE_A}/expert-meetings/types`]: { types: [{ key: "zz_conference", label: "Zz minute", declarations: [], notes: null }] },
  [`/v1/cases/${CASE_A}/expert-meetings/${MEETING.id}`]: MEETING,
  [`/v1/cases/${CASE_A}/expert-meetings/${MEETING.id}/participants`]: { participants: [{ id: "a9888888-8888-4888-8888-888888888888", name: "Dr R", discipline: null, side: "other_side", on_avodeck: false }] },
  [`/v1/cases/${CASE_A}/concordance`]: {
    bundle: { id: "a9333333-3333-4333-8333-333333333333", status: "matched", hearing_id: null, received_on: "2027-06-02", page_count: 3914, version: 1 },
    rows: [{ id: "a9444444-4444-4444-8444-444444444441", page_no: 118, trial_page_no: 2407, status: "matched", method: "text", score: 0.97, confirmed: false, paragraphs: [13], version: 1 }],
    counts: { matched: 1, to_confirm: 0, missing: 0 },
  },
  [`/v1/cases/${CASE_A}/trial-pack/settings`]: { sections: ["report"], include_guidance: false, hearing_id: null, version: 1 },
  [`/v1/cases/${CASE_A}/trial-pack/latest`]: { pack: null, settings: { sections: ["report"], include_guidance: false, hearing_id: null, version: 1 } },
  [`/v1/cases/${CASE_A}/offline-packages`]: { packages: [] },
};

async function stubTrial(page: Page) {
  await stubApi(page, { signedIn: true });
  await page.route(`**/v1/cases/${CASE_A}**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = BODIES[path];
    if (body === undefined) return route.fallback();
    return route.fulfill({ status: 200, contentType: "application/json", headers: { ETag: '"1"' }, body: JSON.stringify(body) });
  });
}

async function checkPage(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  for (const button of await page.getByRole("button").all()) {
    if (await button.isVisible()) expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(52);
  }
  const size = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
  expect(size).toBeGreaterThanOrEqual(17);
}

for (const screen of ["joint-statement", "trial-pages", "trial-pack"]) {
  test(`the ${screen} screen passes axe, with 52px buttons and 17px body text`, async ({ page }) => {
    await stubTrial(page);
    await page.goto(`/cases/${CASE_A}/${screen}`);
    await expect(page.getByRole("button").first()).toBeVisible();
    await checkPage(page);
  });
}

test("the witness-box screen opens with no API and no sign-in", async ({ page }) => {
  const calls: string[] = [];
  await page.route("**/v1/**", (route) => {
    calls.push(route.request().url());
    return route.abort();
  });
  await page.goto(`/cases/${CASE_A}/witness-box`);
  await checkPage(page);
  expect(calls).toEqual([]);
});
