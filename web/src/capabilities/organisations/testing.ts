// Fixtures for slice O's screen tests (shapes come from the generated schema).
import { http, json, server, signedIn } from "../../test/api";
import { me, PERSON_ID, TEAM_ORG, type Schemas } from "../../test/fixtures";

export { PERSON_ID, TEAM_ORG };
export const OTHER = "66666666-6666-4666-8666-666666666666";
export const THIRD = "77777777-7777-4777-8777-777777777777";

export const OWNER_ACTIONS = ["org.billing", "org.close", "org.create_case", "org.export", "org.manage_members", "org.set_policy", "org.view_all_cases", "org.view_members"];
export const ADMIN_ACTIONS = OWNER_ACTIONS.filter((a) => a !== "org.close" && a !== "org.export");

export function org(over: Partial<Schemas["Organisation"]> = {}): Schemas["Organisation"] {
  return { id: TEAM_ORG, name: "Northgate Medico-Legal", kind: "team", region: "uk", role: "owner", allowed_actions: OWNER_ACTIONS, assignable_roles: ["owner", "admin", "expert", "assistant", "viewer"], policy: { required_template_id: null, max_retention_years: 6 }, created_at: "2026-02-01T09:00:00Z", ...over };
}

export function member(id: string, name: string, role: Schemas["OrgMember"]["role"], over: Partial<Schemas["OrgMember"]> = {}): Schemas["OrgMember"] {
  return { membership_id: id, person_id: id, full_name: name, email: `${name.split(" ").join(".").toLowerCase()}@example.test`, role, started_at: "2025-03-01T09:00:00Z", ended_at: null, case_count: 0, ...over };
}

export const MEMBERS = [member(PERSON_ID, "Dr Jane Morgan", "owner"), member(OTHER, "Sarah Okafor", "admin"), member(THIRD, "Lena Hart", "expert")];

export function invite(over: Partial<Schemas["Invite"]> = {}): Schemas["Invite"] {
  return {
    id: "88888888-8888-4888-8888-888888888888",
    email: "k.shah@example.test",
    role: "expert",
    status: "pending",
    invited_by: PERSON_ID,
    created_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    expires_at: new Date(Date.now() + 5 * 86_400_000 + 3_600_000).toISOString(),
    accepted_at: null,
    ...over,
  };
}

export type Sent = { method: string; path: string; body: unknown };

export function recorder() {
  const sent: Sent[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    sent.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
  };
  return { sent, record };
}

/** A signed-in owner of the team, with the team's organisation, people and invites served. */
export function teamWorld(opts: { org?: Schemas["Organisation"]; invites?: Schemas["Invite"][]; members?: Schemas["OrgMember"][] } = {}) {
  const person = me();
  signedIn({
    me: { ...person, memberships: [...person.memberships, { organisation_id: TEAM_ORG, name: "Northgate Medico-Legal", kind: "team", role: "owner", started_at: "2026-02-01T09:00:00Z", member_count: 3 }] },
  });
  const state = { org: opts.org ?? org(), invites: opts.invites ?? [invite()], members: opts.members ?? MEMBERS };
  server.use(
    http.get(`/v1/organisations/${TEAM_ORG}`, () => json(state.org)),
    http.get(`/v1/organisations/${TEAM_ORG}/members`, () => json({ members: state.members })),
    http.get(`/v1/organisations/${TEAM_ORG}/invites`, () => json({ invites: state.invites })),
    http.get(`/v1/organisations/${TEAM_ORG}/billing-profile`, () =>
      json({ profile: { id: OTHER, legal_name: "Northgate Medico-Legal Ltd", address: null, tax_number: null, tax_rate: "0", currency: "GBP", hourly_rate: null, invoice_prefix: "NML" } }),
    ),
    http.get("/v1/me/template", () => json({ id: THIRD, filename: "My template.docx" })),
  );
  return state;
}
