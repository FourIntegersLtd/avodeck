// The e2e runs the built app against a stubbed API (page.route), so it needs no database.
// The stub carries enough to judge S01, S03 and S28 against their boards; party words are
// pack-neutral (rule 11), never the launch pack's own.
import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export const CASE_A = "44444444-4444-4444-8444-444444444444";
export const CASE_B = "55555555-5555-4555-8555-555555555555";
const ORG = "22222222-2222-4222-8222-222222222222";
const TEAM = "33333333-3333-4333-8333-333333333333";
const STEPS = ["prepare", "read", "understand", "write", "court"].map((key) => ({ key, screens: [] }));

const card = (over: Record<string, unknown>) => ({
  case_id: CASE_A,
  label: "Mrs A",
  organisation_id: ORG,
  organisation_name: "Jane Morgan",
  organisation_kind: "personal",
  status: "active",
  stage: "read",
  due_date: "2026-11-30",
  party_role: "other_side",
  jurisdiction: "gb-eaw",
  case_type: "clin-neg-breach",
  specialty: "general-practice",
  your_role: "named_expert",
  on_hold: false,
  ...over,
});

export async function stubApi(page: Page, opts: { signedIn: boolean }) {
  let signedIn = opts.signedIn;
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === "/v1/auth/session") {
      return signedIn
        ? json(route, { aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30 })
        : json(route, { error: { code: "not_signed_in", message: "" } }, 401);
    }
    if (path === "/v1/auth/sign-in" && method === "POST") return json(route, { status: "mfa_required", mfa_enrolled: true });
    if (path === "/v1/auth/mfa/verify" && method === "POST") {
      signedIn = true;
      return json(route, { status: "signed_in", recovery_codes: null });
    }
    if (path === "/v1/cell") return json(route, { storage_place: "the UK" });
    if (path === "/v1/me") {
      return json(route, {
        person: { id: "11111111-1111-4111-8111-111111111111", email: "j.morgan@example.test", full_name: "Dr Jane Morgan", locale: "en-GB", timezone: "Europe/London", home_region: "uk", created_at: "2026-01-05T09:00:00Z", greeting_name: "Dr Morgan", password_changed_at: new Date(Date.now() - 95 * 86_400_000).toISOString() },
        expert: null,
        mfa_enrolled: true,
        memberships: [
          { organisation_id: ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z", member_count: 1 },
          { organisation_id: TEAM, name: "Northgate Medico-Legal", kind: "team", role: "owner", started_at: "2026-02-01T09:00:00Z", member_count: 4 },
        ],
      });
    }
    if (path === "/v1/today") {
      return json(route, {
        cases: [
          card({}),
          card({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM, organisation_name: "Northgate Medico-Legal", organisation_kind: "team", stage: "court", party_role: "instructing_side", due_date: "2026-10-21" }),
        ],
        needs_you: [
          { kind: "reader_pages_to_read", case_id: CASE_A, rank: 1, label_key: "reader.today.pages_to_read", action_key: "reader.today.pages_to_read_action", screen: "S09", count: 127, minutes: 40, due_at: null, params: {} },
          { kind: "understand_things_to_look_at", case_id: CASE_B, rank: 2, label_key: "understand.today.things_to_look_at", action_key: "understand.today.things_to_look_at_action", screen: "S13", count: 2, minutes: null, due_at: "2026-10-26T09:00:00Z", params: {} },
        ],
        organisations: [
          { id: ORG, name: "Jane Morgan", kind: "personal", open_count: 1 },
          { id: TEAM, name: "Northgate Medico-Legal", kind: "team", open_count: 1 },
        ],
        closed_count: 23,
        open_count: 2,
        due_this_month: 0,
        readiness: { account: true, profile: true, signature: true, done_steps: 3, total_steps: 3 },
      });
    }
    if (path === "/v1/notifications") return json(route, { items: [], unread_count: 2, email_bounced: false });
    if (path === "/v1/me/stats/balance") return json(route, { counts: { instructing_side: 14, other_side: 9, joint: 2 }, months: 12 });
    if (path === "/v1/auth/mfa/recovery-codes") return json(route, { remaining: 7, total: 10 });
    if (path === "/v1/me/sessions") {
      return json(route, [
        { id: "66666666-6666-4666-8666-666666666666", device_label: "Chrome on Windows laptop", created_at: "2026-10-01T09:00:00Z", last_seen_at: new Date().toISOString(), current: true },
        { id: "77777777-7777-4777-8777-777777777777", device_label: "Safari on iPad", created_at: "2026-09-01T09:00:00Z", last_seen_at: new Date(Date.now() - 2 * 86_400_000).toISOString(), current: false },
      ]);
    }
    if (path === "/v1/me/notification-preferences") {
      return json(route, [
        { organisation_id: ORG, organisation_name: "Jane Morgan", organisation_kind: "personal", in_app: true, email: true, digest: false, muted_kinds: [] },
        { organisation_id: TEAM, organisation_name: "Northgate Medico-Legal", organisation_kind: "team", in_app: true, email: false, digest: true, muted_kinds: [] },
      ]);
    }
    if (/^\/v1\/cases\/[^/]+\/pack$/.test(path)) {
      return json(route, {
        workflow_steps: STEPS,
        party_roles: [
          { key: "instructing_side", label: "instructing side" },
          { key: "other_side", label: "other side" },
          { key: "joint", label: "joint expert" },
        ],
        vocabulary: {},
        locale: "en-GB",
        timezone: "Europe/London",
      });
    }
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
}
