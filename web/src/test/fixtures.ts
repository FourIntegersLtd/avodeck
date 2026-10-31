// Typed API fixtures for tests. Shapes come from the generated OpenAPI schema, so a contract
// change breaks the type check here first. Pack data comes from the real packs (`make api-client`).
import packFixtures from "../foundation/platform-client/pack-fixtures.json";
import type { components } from "../foundation/platform-client/schema";

export type Schemas = components["schemas"];

export const PERSON_ID = "11111111-1111-4111-8111-111111111111";
export const PERSONAL_ORG = "22222222-2222-4222-8222-222222222222";
export const TEAM_ORG = "33333333-3333-4333-8333-333333333333";
export const CASE_A = "44444444-4444-4444-8444-444444444444";
export const CASE_B = "55555555-5555-4555-8555-555555555555";

export const launchPack = packFixtures.launch.case_pack as unknown as Schemas["CasePack"];
export const fixturePack = packFixtures.fixture.case_pack as unknown as Schemas["CasePack"];

export function me(over: Partial<Schemas["Person"]> = {}): Schemas["Me"] {
  return {
    person: {
      id: PERSON_ID,
      email: "j.morgan@example.test",
      full_name: "Dr Jane Morgan",
      locale: "en-GB",
      timezone: "Europe/London",
      home_region: "uk",
      created_at: "2026-01-05T09:00:00Z",
      ...over,
    },
    expert: null,
    mfa_enrolled: true,
    is_support_staff: false,
    must_change_password: false,
    memberships: [
      { organisation_id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z", member_count: 1 },
    ],
  };
}

export function sessionStatus(over: Partial<Schemas["SessionStatus"]> = {}): Schemas["SessionStatus"] {
  return { aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30, must_change_password: false, ...over };
}

export function caseCard(over: Partial<Schemas["CaseCard"]> = {}): Schemas["CaseCard"] {
  return {
    case_id: CASE_A,
    label: "Mrs A",
    organisation_id: PERSONAL_ORG,
    organisation_name: "Jane Morgan",
    organisation_kind: "personal",
    status: "open",
    stage: "read",
    due_date: "2026-11-30",
    party_role: "other_side",
    jurisdiction: "gb-eaw",
    case_type: "clin-neg-breach",
    specialty: "general-practice",
    your_role: "named_expert",
    on_hold: false,
    ...over,
  };
}

export function today(over: Partial<Schemas["Today"]> = {}): Schemas["Today"] {
  const cases = over.cases ?? [caseCard()];
  return {
    cases,
    needs_you: [],
    organisations: [{ id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 1 }],
    closed_count: 0,
    open_count: cases.length,
    due_this_month: 0,
    readiness: { account: true, profile: false, signature: false, done_steps: 1, total_steps: 3 },
    ...over,
  };
}

export function device(over: Partial<Schemas["Device"]> = {}): Schemas["Device"] {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    device_label: "Chrome on Windows",
    created_at: "2026-10-01T09:00:00Z",
    last_seen_at: new Date().toISOString(),
    current: true,
    ...over,
  };
}

// Messages (GET /v1/notifications). Typed loosely until `make api-client` adds the schema.
export interface Message {
  id: string;
  kind: string;
  organisation_id: string;
  case_id: string | null;
  case_label: string | null;
  created_at: string;
  read_at: string | null;
}

export interface Messages {
  items: Message[];
  unread_count: number;
  email_bounced: boolean;
}

export function message(over: Partial<Message> = {}): Message {
  return {
    id: "88888888-8888-4888-8888-888888888888",
    kind: "bundle_ready",
    organisation_id: PERSONAL_ORG,
    case_id: CASE_A,
    case_label: "Mrs A",
    created_at: new Date(Date.now() - 3_600_000).toISOString(),
    read_at: null,
    ...over,
  };
}

export function messages(over: Partial<Messages> = {}): Messages {
  return { items: [], unread_count: 0, email_bounced: false, ...over };
}
