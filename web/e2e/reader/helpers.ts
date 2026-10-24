// Slice B's e2e stub: the built app against a stubbed API (page.route), under either pack set.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route } from "@playwright/test";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };

export const CASE_A = "44444444-4444-4444-8444-444444444444";
const ORG = "22222222-2222-4222-8222-222222222222";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

type Pack = Record<string, unknown> & { source_types: Array<{ key: string }> };
export const PACKS = [
  { name: "launch packs", pack: packFixtures.launch.case_pack as unknown as Pack },
  { name: "fixture packs", pack: packFixtures.fixture.case_pack as unknown as Pack },
];

/** The cited words' boxes on page 118, as fractions of the page. */
export const BBOXES = [
  { x0: 0.06, y0: 0.2, x1: 0.56, y1: 0.24 },
  { x0: 0.1, y0: 0.4, x1: 0.5, y1: 0.43 },
];

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const pageId = (n: number) => `99999999-9999-4999-8999-${String(n).padStart(12, "0")}`;
const factId = (n: number) => `88888888-8888-4888-8888-${String(n).padStart(12, "0")}`;
// A blank page picture, 1000 x 1400.
const PAGE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1400" viewBox="0 0 1000 1400"><rect width="1000" height="1400" fill="#ffffff"/></svg>';

/** Page 118's words (once read): each cited word's box is the fact's box. */
export const WORDS = [
  { word_id: "p118w1", text: "Dr P consultation", box: BBOXES[0] },
  { word_id: "p118w2", text: "Anusol", box: BBOXES[1] },
];

/** What the stub saw. */
export interface Seen {
  turns: number[];
}

export async function stubReader(page: Page, pack: Pack): Promise<Seen> {
  let read = false;
  let turn = 0;
  const seen: Seen = { turns: [] };
  const LAST = 1231;
  const facts = [
    { id: factId(1), kind: "consultation", fields: { who: "Dr P", how: "Face to face, at the surgery" }, value: "Dr P consultation 15 May 2024", word_ids: ["p118w1"], bbox: BBOXES[0], event_date: "2024-05-15", status: "suggested", version: 1, flagged: false },
    { id: factId(2), kind: "prescription", fields: {}, value: "Anusol HC suppositories", word_ids: ["p118w2"], bbox: BBOXES[1], event_date: "2024-05-15", status: "suggested", version: 1, flagged: false },
  ];
  const queue = [612, 640].map((n) => ({ page_id: pageId(n), page_no: n, source_type: pack.source_types[0]!.key, handwriting: true, relevance: null, has_transcription: false, needs_clearer_copy: false, done: false }));
  const detail = (n: number) => ({
    page_id: pageId(n), page_no: n, source_type: pack.source_types[0]!.key, read: read && n === 118, in_period: true, unreadable: n > 600, rotation: 0, turn: n === 118 ? turn : 0, relevance: null,
    needs_clearer_copy: false, superseded_by_page_no: null, duplicate_of_page_no: null, handwriting: null, has_transcription: false, first_read_at: null,
  });
  const theCase = {
    id: CASE_A, organisation_id: ORG, named_expert_id: "11111111-1111-4111-8111-111111111111", jurisdiction: "gb-eaw", case_type: "clin-neg-breach",
    specialty: "general-practice", expert_role: "single", label: "Mrs A", party_role: "other_side", instructing_party_type: "lawyer",
    court_track: null, due_date: "2026-11-30", status: "open", on_hold: false, created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z",
    allowed_actions: ["case.view_metadata", "case.view_content", "fact.verify", "page.read", "page.rotate"],
  };
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
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
    if (path === `${c}/progress`) return json(route, { read: 1104, total: 1231, seconds_left: 7620 });
    if (path === `${c}/pages/unreadable`) return json(route, { pages: queue, done: 0, total: queue.length });
    if (path === `${c}/pages` && method === "GET") {
      const query = new URL(route.request().url()).searchParams;
      const from = Number(query.get("cursor") ?? 0) + 1;
      const to = Math.min(LAST, from + Number(query.get("limit") ?? 100) - 1);
      const pages = [];
      for (let n = from; n <= to; n++) pages.push({ page_no: n, page_id: pageId(n), source_type: null, read: n < 118 || (read && n === 118), in_period: null, unreadable: false, duplicate_of_page_no: null, turn: 0 });
      return json(route, { pages, next_cursor: to < LAST ? to : null });
    }
    if (path === `${c}/search` && method === "POST") {
      const box = { x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.25 };
      return json(route, { pages: [{ page_no: 12, hits: 3, boxes: [box] }, { page_no: 40, hits: 1, boxes: [box] }], total_hits: 4 });
    }
    const m = path.match(/\/pages\/(\d+)(\/\w+)?$/);
    if (m) {
      const n = Number(m[1]);
      const tail = m[2] ?? "";
      if (tail === "/image") return route.fulfill({ status: 200, contentType: "image/svg+xml", body: PAGE_SVG });
      if (tail === "/read") {
        read = true;
        return json(route, { page_no: n, read: true, first_read_at: "2026-10-18T10:00:00Z" });
      }
      if (tail === "/turn" && method === "PUT") {
        turn = Number((route.request().postDataJSON() as { degrees: number }).degrees);
        seen.turns.push(turn);
        return json(route, detail(n));
      }
      if (tail === "/words") return read ? json(route, { words: WORDS, reading: WORDS.map((x) => x.text).join(" ") }) : json(route, { error: { code: "blind_read", message: "" } }, 423);
      if (tail === "/facts") return read ? json(route, { facts }) : json(route, { error: { code: "blind_read", message: "" } }, 423);
      if (tail === "") return json(route, detail(n));
    }
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
  return seen;
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
