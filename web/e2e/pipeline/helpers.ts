// Slice A's e2e stub: the built app against a stubbed API (page.route), under either pack set.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route } from "@playwright/test";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

export const CASE_A = "44444444-4444-4444-8444-444444444444";
const ORG = "22222222-2222-4222-8222-222222222222";
const JOB = "77777777-7777-4777-8777-777777777777";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

type Pack = Record<string, unknown> & { source_types: Array<{ key: string }>; instructing_party_types: Array<{ key: string }>; vocabulary: Record<string, unknown> };
export const PACKS = [
  { name: "launch packs", pack: packFixtures.launch.case_pack as unknown as Pack },
  { name: "fixture packs", pack: packFixtures.fixture.case_pack as unknown as Pack },
];

export const askLabel = (pack: Pack) => (pack.vocabulary.instructing_party_request as { label: string }).label;

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const pageId = (n: number) => `99999999-9999-4999-8999-${String(n).padStart(12, "0")}`;
// A 1x1 grey JPEG.
const JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=",
  "base64",
);

export async function stubPipeline(page: Page, pack: Pack) {
  const types = pack.source_types;
  const suggestions = [412, 413, 414, 415].map((n) => ({ page_id: pageId(n), page_no: n, status: "suggested", reason: "identifiers_differ", job_id: JOB }));
  const summary = {
    pages: 1231,
    by_source: types.map((s, i) => ({ source_type: s.key, pages: i === 0 ? 1231 - 100 * (types.length - 1) : 100 })),
    duplicates: 37,
    unreadable: 31,
    gaps: 1,
    missing_items: 1,
    documents: [{ id: "d1", kind: "original", filename: "records.pdf", pages: 1231, first_page_no: 1, last_page_no: 1231, received_on: "2026-10-02", obtained_how: "Subject access request", bundle_version: 1 }],
    versions: [{ bundle_version: 1, documents: 1, pages: 1231, first_page_no: 1, last_page_no: 1231, added_at: "2026-10-02T09:00:00Z" }],
    quarantine_suggestions: suggestions,
    quarantined: [],
    exhibits: [{ id: "e1", document_id: "d2", filename: "call.m4a", media_type: "audio/mp4", size: 1024, part_of: null, added_at: "2026-10-02T09:00:00Z" }],
    foreign_language: [{ page_id: pageId(903), page_no: 903, language: "cy" }],
  };
  const theCase = {
    id: CASE_A, organisation_id: ORG, named_expert_id: "11111111-1111-4111-8111-111111111111", jurisdiction: "gb-eaw", case_type: "clin-neg-breach",
    specialty: "general-practice", expert_role: "single", label: "Mrs A", party_role: "other_side", instructing_party_type: pack.instructing_party_types[0]!.key,
    court_track: null, due_date: "2026-11-30", status: "open", on_hold: false, created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z",
    allowed_actions: ["case.view_metadata", "case.view_content", "case.upload", "fact.verify", "page.quarantine", "page.review_duplicate", "request.draft"],
  };
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
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
    if (path === `${c}/pack`) return json(route, pack);
    if (path === `${c}/process`) return json(route, { job: { id: JOB, kind: "process_bundle", status: "running", step: "sort", pct: 48, eta_seconds: 220, error_code: null } });
    if (path === `/v1/jobs/${JOB}/events`) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: `retry: 60000\ndata: ${JSON.stringify({ status: "running", step: "sort", pct: 48, eta_seconds: 220, error_code: null })}\n\n` });
    }
    if (path === `${c}/bundle/summary`) return json(route, summary);
    if (path === `${c}/missing-items`) {
      return json(route, { missing_items: [{ id: "m1", citing_page_id: pageId(1118), citing_page_no: 1118, word_ids: ["w1"], description: "Colonoscopy report from September", reference_date: "2024-09-24", status: "open", reason: null, received_page_id: null, received_page_no: null, version: 1 }] });
    }
    if (path === `${c}/duplicates`) {
      return json(route, { duplicates: [{ page_id: pageId(20), page_no: 20, copy_of_page_id: pageId(3), copy_of_page_no: 3, status: "suggested", version: 1 }] });
    }
    if (path.startsWith(`${c}/pages/`) && path.endsWith("/thumbnail")) return route.fulfill({ status: 200, contentType: "image/jpeg", body: JPEG });
    if (path === `${c}/instructing-party-requests/draft` && method === "POST") {
      return json(route, {
        label: askLabel(pack), subject: "Records needed - Mrs A", body: "Please supply:\n- Colonoscopy report from September", missing_item_ids: ["m1"],
        clearer_copy_page_ids: [], clearer_copy_page_nos: [], translation_page_ids: [], translation_page_nos: [], unreadable_document_ids: [], unreadable_document_names: [],
      });
    }
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
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
