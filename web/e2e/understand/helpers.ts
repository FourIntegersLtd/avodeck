// Slice D's e2e stub: the built app against a stubbed API (page.route), under either pack set.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route } from "@playwright/test";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

export const CASE_A = "44444444-4444-4444-8444-444444444444";
export const FACT_1 = "f1111111-1111-4111-8111-111111111111";
const ORG = "22222222-2222-4222-8222-222222222222";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

type Pack = Record<string, unknown> & { vocabulary: Record<string, unknown> };
export const PACKS = [
  { name: "launch packs", pack: packFixtures.launch.case_pack as unknown as Pack, jurisdiction: packFixtures.launch.jurisdiction.code, caseType: "clin-neg-breach", specialty: "general-practice" },
  { name: "fixture packs", pack: packFixtures.fixture.case_pack as unknown as Pack, jurisdiction: packFixtures.fixture.jurisdiction.code, caseType: "zz-case", specialty: "zz-specialty" },
];
export type Run = (typeof PACKS)[number];

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const event = (n: number, over: Record<string, unknown> = {}) => ({
  fact_id: n === 0 ? FACT_1 : uuid(n), kind: "consultation", page_no: n + 1, event_date: `2024-0${(n % 6) + 3}-1${(n % 8) + 1}`, available_at: "2024-05-15", status: n % 2 ? "suggested" : "verified", summary: `Entry ${n + 1}`, source: "src_a", question_ids: n % 3 ? [] : ["a1111111-1111-4111-8111-111111111111"], has_finding: n === 1, ...over,
});

export interface Stub {
  /** When the timeline answer was sent (ms since the epoch). */
  timelineSentAt: () => number;
}

/** Segmented view control: the real radio is visually hidden, so click its label. */
export async function showTimelineMap(page: Page) {
  await page.locator("label.av-segmented__option", { hasText: "Overview map" }).click();
  await expect(page.getByTestId("timeline-board")).toBeVisible();
}

export async function stubUnderstand(page: Page, run: Run, opts: { events?: number } = {}): Promise<Stub> {
  let sentAt = 0;
  const total = opts.events ?? 6;
  const lanes = [0, 1, 2].map((lane) => ({
    key: `lane_${lane}`,
    label: `Line ${lane + 1}`,
    colour: ["#1E6B4E", "#4C6E91", "#7A6BA8"][lane],
    icon: ["visit", "flask", "moon"][lane],
    subtitle: `${Math.ceil((total - lane) / 3)} entries here`,
    events: Array.from({ length: total }, (_, i) => i).filter((i) => i % 3 === lane).map((i) => event(i)),
  }));
  const theCase = {
    id: CASE_A, organisation_id: ORG, named_expert_id: "11111111-1111-4111-8111-111111111111", jurisdiction: run.jurisdiction, case_type: run.caseType,
    specialty: run.specialty, expert_role: "single", label: "Mrs A", party_role: "other_side", instructing_party_type: null, court_track: null, due_date: "2026-11-30",
    status: "open", on_hold: false, created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z", allowed_actions: ["case.view_content", "finding.decide", "consultation.note", "comment.write"],
  };
  const entry = (kind: string, pageNo: number, available: string | null) => ({ fact_id: uuid(900 + pageNo), kind, page_no: pageNo, event_date: "2024-05-10", available_at: available, status: "verified", summary: null, source: null, has_finding: false });
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const c = `/v1/cases/${CASE_A}`;
    if (path === "/v1/auth/session") return json(route, { aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30 });
    if (path === "/v1/me") {
      return json(route, {
        person: { id: "11111111-1111-4111-8111-111111111111", email: "j.morgan@example.test", full_name: "Dr Jane Morgan", locale: "en-GB", timezone: "Europe/London", home_region: "uk", created_at: "2026-01-05T09:00:00Z" },
        expert: null,
        mfa_enrolled: true,
        memberships: [{ organisation_id: ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z" }],
      });
    }
    if (path === "/v1/notifications") return json(route, { items: [], unread_count: 0, email_bounced: false });
    if (path === c) return json(route, theCase);
    if (path === `${c}/pack`) return json(route, run.pack);
    if (path === `${c}/questions`) return json(route, { questions: [{ id: "a1111111-1111-4111-8111-111111111111", number: 1, text_verbatim: "Q", confirmed_text: null, kind: "x", outside_expertise: false, para_ref: null, status: "confirmed", version: 1, source_word_ids: [], instruction_version_id: "b1111111-1111-4111-8111-111111111111" }], assumptions: [], locked: false, reading: false, reopened: false, rejected: 0, warnings: [], version_no: 1, instruction_version_id: "b1111111-1111-4111-8111-111111111111" });
    if (path === `${c}/timeline`) {
      sentAt = Date.now();
      return json(route, { as_seen_on: null, as_seen_title: null, range: { first: "2024-03-11", last: "2024-08-18" }, sources: [{ key: "src_a", label: "Source A", colour: "#1E6B4E" }], counts: { checked: 3, please_check: 3, things_to_look: 1, not_linked: 4, undated: 1, total: 6 }, lanes, unplaced: [event(7000, { event_date: null, kind: "letter" })] });
    }
    if (path === `${c}/consultations/${FACT_1}`) {
      return json(route, {
        title: String((run.pack.vocabulary as Record<string, unknown>).as_seen_title ?? "Seen on the day").replace(/\{[a-z_]+\}/g, "reader"),
        seen_title: "What the reader had in front of them",
        unseen_title: "Not in front of the reader",
        page_marks: [
          { fact_id: FACT_1, role: "entry", box: { x0: 0.08, y0: 0.35, x1: 0.92, y1: 0.5 } },
          { fact_id: uuid(901), role: "later", box: { x0: 0.08, y0: 0.58, x1: 0.92, y1: 0.7 } },
        ],
        entry: { ...entry("consultation", 118, "2024-05-15"), fact_id: FACT_1, event_date: "2024-05-15", author_line: "Dr P · face to face" },
        visible: [entry("result", 117, "2024-05-14")],
        on: "2024-05-15",
        question_numbers: [2, 3],
        later_count: 1,
        later: [entry("letter", 301, "2024-05-22")],
        unknown: [entry("referral", 150, null)],
        guidance: [{ code: "G1", title: "Guidance title", version_label: "2023 edition", url: "https://example.test/g1" }],
        note: null,
      });
    }
    if (path === `${c}/consultations/${FACT_1}/later-notes`) return json(route, { notes: [] });
    if (path.startsWith(`${c}/pages/`) && path.endsWith("/image")) {
      return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64") });
    }
    if (path === `${c}/comments`) return json(route, { comments: [] });
    if (path === `${c}/findings`) {
      return json(route, { findings: [
        { id: "c1111111-1111-4111-8111-111111111111", check_key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, evidence_fact_ids: [], evidence: [{ fact_id: uuid(902), kind: "result", page_no: 214, event_date: "2024-02-19", summary: "Hb 98" }], evidence_pages: [214], decision: null, reason: null, version: 1, title: "An abnormal result had no action recorded", icon: "flask" },
        { id: "c2222222-2222-4222-8222-222222222222", check_key: "zz_session_length_recorded", rule: "field_missing", gentle: true, evidence_fact_ids: [], evidence: [{ fact_id: uuid(903), kind: "consultation", page_no: 1071, event_date: "2024-06-03", summary: "Repeat" }], evidence_pages: [1071], decision: "not_relevant", reason: "Seen on the ward", version: 2, title: "No session length recorded", icon: "visit" },
      ] });
    }
    if (path === "/v1/me/checks") return json(route, { checks: [{ key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, default_on: true, enabled: true, window_days: 14 }] });
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
  return { timelineSentAt: () => sentAt };
}

/** axe with colour contrast, buttons at least 52px tall and body text at least 17px. */
export async function checkScreen(page: Page) {
  const axe = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(axe.violations).toEqual([]);
  const short = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("button, a.av-button"))
      .filter((el) => el.offsetParent !== null)
      .filter((el) => el.getBoundingClientRect().height < 52)
      .map((el) => el.textContent?.trim()),
  );
  expect(short).toEqual([]);
  const small = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("body, main p, main li, main td, main th, main label"))
      .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 17)
      .map((el) => el.tagName + ":" + (el.textContent ?? "").trim().slice(0, 30)),
  );
  expect(small).toEqual([]);
}
