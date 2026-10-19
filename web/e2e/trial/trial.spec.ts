// S22-S25 acceptance: each trial screen at its route, with axe (colour contrast included) and the size
// rules. The API is stubbed: the foundation stub, plus the trial endpoints. The witness-box screen gets no
// API at all (it works from the copy on the device).
import AxeBuilder from "@axe-core/playwright";
import { webcrypto } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { CASE_A, stubApi } from "../foundation/helpers";

const ACTIONS = ["case.view_metadata", "case.view_content", "case.edit_setup", "case.upload", "opinion.write", "fact.verify", "report.build", "report.sign", "request.draft", "case.export"];
const CASE = { id: CASE_A, label: "Mrs A", status: "served", case_type: "clin-neg-breach", due_date: "2027-06-14", instructing_party_type: "lawyer", allowed_actions: ACTIONS };
const PACK = {
  workflow_steps: [
    { key: "prepare", screens: ["bundle_ready"] },
    { key: "read", screens: ["reader"] },
    { key: "understand", screens: ["timeline"] },
    { key: "write", screens: ["opinion"] },
    { key: "court", screens: ["after_serving", "expert_meeting", "court_bundle_pages", "trial_pack", "witness_box"] },
  ],
  party_roles: [], instructing_party_types: [{ key: "lawyer", label: "solicitor" }], fact_kinds: [], locale: "en-GB", timezone: "Europe/London", date_order: "DMY",
  vocabulary: { court_bundle: "trial bundle", expert_meeting: "Zz meeting", expert_meeting_document: "Zz minute", source_types: { letters: "Hospital letter", primary_care_record: "Consultation" } },
};
const MEETING = {
  id: "a9111111-1111-4111-8111-111111111111", type: "zz_conference", label: "Zz minute", status: "draft", hearing_id: null,
  discussion_on: "2027-02-12", sign_by: "2027-02-19", sign_rule_ref: "Rule Z.9", send_by: "2027-02-26", send_rule_ref: "Rule Z.10",
  declarations: ["We understand our duty to the court and have complied with it.", "Neither of us has been instructed to avoid reaching agreement."], prepared_sha256: null, prepared_pages: null, certificate_sha256: null, signed_sha256: null, version: 1,
  items: [
    { id: "a9222222-2222-4222-8222-222222222221", number: 1, text: "Did Dr P need to examine Mrs A on 15 May 2024?", own_position: "Yes. Bleeding with known anaemia called for abdominal and rectal examination.", other_positions: "Yes, examination was required.", agreed: true, reason: null, change_note: null, deviation: "consistent", deviation_reason: "same_words", served_paragraphs: [13], pages: [118], changed_from_previous: "same", version: 1 },
    { id: "a9222222-2222-4222-8222-222222222222", number: 2, text: "Was the February result acted on appropriately?", own_position: "No. Iron was started without looking for a cause.", other_positions: "Within the range: iron first, review later, was reasonable.", agreed: false, reason: "We differ on whether a cause should have been sought at once.", change_note: null, deviation: "consistent", deviation_reason: "copied_from_report", served_paragraphs: [4], pages: [214, 87], changed_from_previous: "same", version: 1 },
    { id: "a9222222-2222-4222-8222-222222222223", number: 3, text: "By what date should Mrs A have been referred?", own_position: "By 15 May 2024 at the latest.", other_positions: "By 15 May 2024.", agreed: true, reason: null, change_note: null, deviation: "changed", deviation_reason: "differs_from_report", served_paragraphs: [14], pages: [118], changed_from_previous: "same", version: 1 },
  ],
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
    bundle: { id: "a9333333-3333-4333-8333-333333333333", status: "matched", hearing_id: null, received_on: "2027-06-02", replaces_received_on: "2027-05-20", page_count: 3914, version: 2 },
    rows: [
      { id: "a9444444-4444-4444-8444-444444444441", page_no: 87, trial_page_no: 2376, status: "matched", method: "text", score: 0.97, confirmed: false, paragraphs: [12], source_type: "primary_care_record", document_date: "2024-03-01", version: 1 },
      { id: "a9444444-4444-4444-8444-444444444442", page_no: 118, trial_page_no: 2407, status: "matched", method: "text", score: 0.97, confirmed: false, paragraphs: [13], source_type: "primary_care_record", document_date: "2024-05-15", version: 1 },
      { id: "a9444444-4444-4444-8444-444444444443", page_no: 214, trial_page_no: null, status: "missing", method: "none", score: 0, confirmed: false, paragraphs: [11, 4], source_type: "letters", document_date: "2024-02-20", version: 1 },
      { id: "a9444444-4444-4444-8444-444444444444", page_no: 1118, trial_page_no: 3102, status: "matched", method: "text", score: 0.97, confirmed: false, paragraphs: [19], source_type: "letters", document_date: "2024-09-24", version: 1 },
    ],
    counts: { matched: 3, to_confirm: 0, missing: 1 },
  },
  [`/v1/cases/${CASE_A}/trial-pack/settings`]: { sections: ["report", "chronology", "readiness", "joint_statement", "legal_tests"], include_guidance: false, hearing_id: null, version: 1 },
  [`/v1/cases/${CASE_A}/trial-pack/latest`]: {
    pack: { id: "a9555555-5555-4555-8555-555555555555", status: "ready", sha256: "c".repeat(64), page_count: 54, sections: ["report"], stale: false, stale_reason: null, created_at: "2027-06-03T09:00:00Z", section_pages: [] },
    settings: { sections: ["report"], include_guidance: false, hearing_id: null, version: 1 },
  },
  [`/v1/cases/${CASE_A}/trial-pack/preview`]: {
    sections: [{ key: "report", pages: 31 }, { key: "chronology", pages: 9 }, { key: "readiness", pages: 3 }, { key: "joint_statement", pages: 6 }, { key: "legal_tests", pages: 1 }, { key: "guidance", pages: 4 }],
    total_pages: 54,
    sheets: [{ number: 2, text: "The visit on 15 May 2024", answer: "Rectal bleeding with known iron-deficiency anaemia called for an abdominal and rectal examination and a two-week-wait referral. Neither happened. (para 6.13-6.14)", asked: "\"He may have examined her and not written it down.\"", agreed: true, reason: null, pages: [{ page_no: 118, trial_page_no: 2407, status: "matched" }], reply: "Referral was needed whatever an exam showed.", replied_at: "2027-06-01T08:00:00Z" }, { number: 3, text: "Next", answer: null, asked: null, agreed: null, reason: null, pages: [] }],
  },
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

// The copy as the API seals it (AES-GCM, AAD "avodeck-offline:{id}:{format}"), so the real device store and
// search run in the browser: made, put on this device behind a PIN, opened, searched, with no further call.
async function sealedCopy(id: string) {
  const content = {
    format_version: 1, case_id: CASE_A, built_at: "2027-06-03T09:00:00Z", date_order: "DMY", court_bundle: "trial bundle",
    report: { version_no: 1, served_on: "2027-01-10", paragraphs: [{ para_no: 13, section: "opinion", text: "Rectal bleeding on 15 May 2024 called for an examination.", pages: [{ page_no: 118, trial_page_no: 2407 }] }] },
    facts: [
      { kind: "consultation", text: "Problem: Haemorrhoids (new). History: c/o bright red PR bleeding x3/52, on paper. Tired. Thinks piles. Plan: fybogel, anusol. Rv if persists.", date: "2024-05-15", page_no: 118, trial_page_no: 2407 },
      { kind: "letter", text: "Reports intermittent rectal bleeding since around 15 May, seen by GP at that time.", date: "2024-09-24", page_no: 1118, trial_page_no: 3102 },
    ],
    concordance: [], joint_statement: null, answers: [],
  };
  const raw = webcrypto.getRandomValues(new Uint8Array(32));
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const key = await webcrypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt"]);
  const data = await webcrypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(`avodeck-offline:${id}:1`) }, key, new TextEncoder().encode(JSON.stringify(content)));
  const b64 = (b: ArrayBuffer | Uint8Array) => Buffer.from(b instanceof Uint8Array ? b : new Uint8Array(b)).toString("base64");
  return { id, case_id: CASE_A, format_version: 1, key: b64(raw), iv: b64(iv), ciphertext: b64(data) };
}

test("the witness-box search finds entries in the copy on this device, with no call to the API", async ({ page }) => {
  const id = "a9666666-6666-4666-8666-666666666666";
  const copy = { id, device_label: "My tablet", hearing_id: null, format_version: 1, include_images: false, sha256: "d".repeat(64), status: "built", expires_at: "2099-07-01T09:00:00Z", downloaded_at: null, revoked_at: null, created_at: "2027-06-03T09:00:00Z" };
  let made = false;
  await stubTrial(page);
  await page.route(`**/v1/cases/${CASE_A}/offline-packages**`, async (route) => {
    const { pathname } = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (pathname.endsWith("/status")) return json({ status: "downloaded" });
    if (pathname.endsWith("/download")) { copy.status = "downloaded"; return json(await sealedCopy(id)); }
    if (route.request().method() === "POST") { made = true; return json(copy, 201); }
    return json({ packages: made ? [copy] : [] });
  });
  await page.goto(`/cases/${CASE_A}/trial-pack`);
  await page.getByRole("button", { name: "Copy for the witness box" }).click();
  await page.getByLabel("Name of this device").fill("My tablet");
  await page.getByRole("button", { name: "Make a copy for this device" }).click();
  await page.getByLabel(/Choose a PIN/).fill("246810");
  await page.getByLabel("Type the PIN again").fill("246810");
  await page.getByRole("button", { name: "Put the copy on this device" }).click();
  await page.getByRole("link", { name: "Open the witness box search" }).click();
  await page.getByLabel("Your PIN for this copy").fill("246810");
  const calls: string[] = [];
  await page.route("**/v1/**", (route) => { calls.push(route.request().url()); return route.abort(); });
  await page.getByRole("button", { name: "Open the copy" }).click();
  await page.getByLabel("Type a date, some words, or a page number").fill("15 May");
  await expect(page.getByText("3 entries")).toBeVisible();
  await checkPage(page);
  expect(calls.filter((url) => !url.endsWith("/status"))).toEqual([]);
});
