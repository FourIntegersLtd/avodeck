// Test stand-ins for slice C's screens: typed API bodies built from the generated schema, so a contract
// change breaks these first. Imported by the setup tests only.
import packFixtures from "../../foundation/platform-client/pack-fixtures.json";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_A, me, PERSON_ID, PERSONAL_ORG } from "../../test/fixtures";

export type PackRun = "launch" | "fixture";

/** The launch pack's own words (party, instructing party, clinician, declaration): none may show under the fixture packs. */
export function launchWords(): RegExp {
  const pack = packFixtures.launch.case_pack as unknown as Schemas["CasePack"];
  const v = pack.vocabulary as Record<string, unknown>;
  const words = [
    ...pack.party_roles.map((r) => r.label!),
    ...pack.instructing_party_types.map((r) => r.label!),
    String(v.clinician),
    String(v.clinicians),
    String(v.declaration_name),
    String(v.court_bundle),
  ];
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b(${words.map(escape).join("|")})\\b`, "i");
}

export function casePack(run: PackRun): Schemas["CasePack"] {
  return packFixtures[run].case_pack as unknown as Schemas["CasePack"];
}

export function jurisdiction(run: PackRun): Schemas["JurisdictionSummary"] {
  return packFixtures[run].jurisdiction as unknown as Schemas["JurisdictionSummary"];
}

export function combination(run: PackRun): Schemas["PackCombination"] {
  const j = jurisdiction(run);
  const versions = casePack(run).versions;
  const ref = (code: string, name: string) => ({ code, name, version: versions[code] ?? "1" });
  return run === "launch"
    ? {
        jurisdiction: ref(j.code, j.name),
        case_type: ref("clin-neg-breach", "Case type A"),
        specialty: ref("general-practice", "Specialty A"),
        expert_roles: ["breach"],
      }
    : {
        jurisdiction: ref(j.code, j.name),
        case_type: ref("zz-case", "Test case"),
        specialty: ref("zz-specialty", "Test specialty"),
        expert_roles: ["assessor"],
      };
}

export function caseItem(run: PackRun, over: Partial<Schemas["Case"]> = {}): Schemas["Case"] {
  const combo = combination(run);
  return {
    id: CASE_A,
    label: "Mrs A",
    organisation_id: PERSONAL_ORG,
    named_expert_id: PERSON_ID,
    jurisdiction: combo.jurisdiction.code,
    case_type: combo.case_type.code,
    specialty: combo.specialty.code,
    expert_role: combo.expert_roles[0]!,
    party_role: "other_side",
    instructing_party_type: null,
    court_track: null,
    due_date: "2026-11-30",
    status: "open",
    on_hold: false,
    allowed_actions: [],
    period_start: "2024-02-01",
    period_end: "2024-08-31",
    subject_status: null,
    created_at: "2026-10-02T09:00:00Z",
    updated_at: "2026-10-02T09:00:00Z",
    ...over,
  };
}

/** Signed in, with one case open under the given pack run. */
export function inCase(run: PackRun, over: Partial<Schemas["Case"]> = {}) {
  signedIn({ me: me(), pack: casePack(run) });
  server.use(
    http.get("/v1/cases/:caseId", () => json(caseItem(run, over), 200)),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction(run))),
    http.get("/v1/me/stats/balance", () => json({ counts: { other_side: 14, instructing_side: 9 }, months: 12 })),
  );
}

export function question(over: Partial<Schemas["QuestionOut"]> = {}): Schemas["QuestionOut"] {
  return {
    id: "a1111111-1111-4111-8111-111111111111",
    number: 1,
    text_verbatim: "Whether the blood results of 19 February 2024 were acted on appropriately.",
    confirmed_text: null,
    kind: "breach",
    outside_expertise: false,
    para_ref: "4.1",
    status: "suggested",
    version: 1,
    source_word_ids: ["p2-w10"],
    instruction_version_id: "b1111111-1111-4111-8111-111111111111",
    ...over,
  };
}

export function assumption(over: Partial<Schemas["AssumptionOut"]> = {}): Schemas["AssumptionOut"] {
  return {
    id: "c1111111-1111-4111-8111-111111111111",
    number: 1,
    text_verbatim: "You should assume that Mrs A attended every appointment recorded.",
    confirmed_text: null,
    para_ref: "6",
    status: "suggested",
    version: 1,
    source_word_ids: ["p2-w90"],
    instruction_version_id: "b1111111-1111-4111-8111-111111111111",
    ...over,
  };
}

export function questions(over: Partial<Schemas["Questions"]> = {}): Schemas["Questions"] {
  return {
    questions: [question()],
    assumptions: [assumption()],
    locked: false,
    reading: false,
    reopened: false,
    rejected: 0,
    warnings: [],
    version_no: 1,
    instruction_version_id: "b1111111-1111-4111-8111-111111111111",
    ...over,
  };
}

export function note(topic: Schemas["NoteDraft"]["topic"], over: Partial<Schemas["NoteDraft"]> = {}): Schemas["NoteDraft"] {
  return {
    topic,
    subject_key: `setup.notes.${topic}.subject`,
    body_key: `setup.notes.${topic}.body`,
    items: [],
    joint: false,
    recipients: [{ id: "d1111111-1111-4111-8111-111111111111", name: "Ms Rees", firm: "Rees & Co", email: null, type_key: "lawyer" }],
    ...over,
  };
}
