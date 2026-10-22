// What every signed-in screen needs from the API, so a screen file only lists its own data.
// Keys are Playwright URL globs, values the JSON answer. Shapes follow src/test/fixtures.ts.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const packs = JSON.parse(readFileSync(join(here, "../../src/foundation/platform-client/pack-fixtures.json"), "utf8"));

export const PERSONAL_ORG = "22222222-2222-4222-8222-222222222222";
export const CASE_A = "44444444-4444-4444-8444-444444444444";

export function sessionMocks() {
  return {
    "/v1/auth/session": { aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30 },
    "/v1/me": {
      person: { id: "11111111-1111-4111-8111-111111111111", email: "j.morgan@example.test", full_name: "Dr Jane Morgan", locale: "en-GB", timezone: "Europe/London", home_region: "uk", created_at: "2026-01-05T09:00:00Z" },
      expert: null,
      mfa_enrolled: true,
      memberships: [{ organisation_id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z" }],
    },
    "/v1/today": { cases: [], needs_you: [], organisations: [{ id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal" }], closed_count: 0 },
    "/v1/cases/*/pack": packs.launch.case_pack,
    "/v1/cases/*/steps/progress": { steps: [] },
    "/v1/cases/*/progress": { read: 0, total: 0, seconds_left: 0 },
    "/v1/notifications": { items: [], unread_count: 0, email_bounced: false },
    "/v1/me/notification-preferences": [],
    "/v1/me/stats/balance": { counts: {}, months: 12 },
    "/v1/auth/mfa/recovery-codes": { remaining: 10, total: 10 },
    "/v1/cell": { storage_place: null, support_phone: null, support_hours: null },
  };
}
