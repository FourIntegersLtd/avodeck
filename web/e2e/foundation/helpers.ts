// The e2e runs the built app against a stubbed API (page.route), so it needs no database.
import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export const CASE_A = "44444444-4444-4444-8444-444444444444";
const ORG = "22222222-2222-4222-8222-222222222222";

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
    if (path === "/v1/me") {
      return json(route, {
        person: { id: "11111111-1111-4111-8111-111111111111", email: "j.morgan@example.test", full_name: "Dr Jane Morgan", locale: "en-GB", timezone: "Europe/London", home_region: "uk", created_at: "2026-01-05T09:00:00Z" },
        expert: null,
        mfa_enrolled: true,
        memberships: [{ organisation_id: ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z" }],
      });
    }
    if (path === "/v1/today") {
      return json(route, {
        cases: [{ case_id: CASE_A, label: "Mrs A", organisation_id: ORG, organisation_name: "Jane Morgan", organisation_kind: "personal", status: "active", stage: "read", due_date: "2026-11-30", party_role: "other_side", jurisdiction: "gb-eaw", case_type: "clin-neg-breach", specialty: "general-practice", your_role: "named_expert", on_hold: false }],
        needs_you: [],
        organisations: [{ id: ORG, name: "Jane Morgan", kind: "personal" }],
        closed_count: 0,
      });
    }
    if (path === `/v1/cases/${CASE_A}/pack`) {
      return json(route, { workflow_steps: [{ key: "prepare", screens: [] }, { key: "read", screens: [] }], party_roles: [{ key: "other_side", label: "the other side" }], vocabulary: {}, locale: "en-GB", timezone: "Europe/London" });
    }
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
}
