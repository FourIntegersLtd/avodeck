// Test stand-ins for slice D's screens: typed API bodies built from the generated schema, so a contract
// change breaks these first. Imported by the understand tests only.
import packFixtures from "../../foundation/platform-client/pack-fixtures.json";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_A, me, PERSON_ID, PERSONAL_ORG } from "../../test/fixtures";

export type PackRun = "launch" | "fixture";

export const FACT_1 = "f1111111-1111-4111-8111-111111111111";
export const FACT_2 = "f2222222-2222-4222-8222-222222222222";
export const FACT_3 = "f3333333-3333-4333-8333-333333333333";
export const Q_1 = "a1111111-1111-4111-8111-111111111111";
export const Q_2 = "a2222222-2222-4222-8222-222222222222";

export function casePack(run: PackRun): Schemas["CasePack"] {
  return packFixtures[run].case_pack as unknown as Schemas["CasePack"];
}

/** The launch pack's own clinician words: none may show under the fixture packs. */
export function launchWords(): RegExp {
  const v = casePack("launch").vocabulary as Record<string, unknown>;
  const words = [String(v.clinician), String(v.clinicians)].filter(Boolean);
  return new RegExp(`\\b(${words.join("|")})\\b`, "i");
}

/** What the named expert may do on slice D's screens (docs/spec/permissions.md). */
export const EXPERT_ACTIONS = ["case.view_content", "finding.decide", "consultation.note", "availability.override", "link.suggest", "link.decide", "comment.write"];
/** An assistant reads, comments and asks for suggestions; nothing else. */
export const ASSISTANT_ACTIONS = ["case.view_content", "link.suggest", "comment.write"];

function caseItem(run: PackRun, actions: string[]): Schemas["Case"] {
  const j = packFixtures[run].jurisdiction as unknown as Schemas["JurisdictionSummary"];
  const launch = run === "launch";
  return {
    id: CASE_A,
    label: "Mrs A",
    organisation_id: PERSONAL_ORG,
    named_expert_id: PERSON_ID,
    jurisdiction: j.code,
    case_type: launch ? "clin-neg-breach" : "zz-case",
    specialty: launch ? "general-practice" : "zz-specialty",
    expert_role: launch ? "breach" : "assessor",
    party_role: "other_side",
    instructing_party_type: null,
    court_track: null,
    due_date: "2026-11-30",
    status: "open",
    on_hold: false,
    allowed_actions: actions,
    period_start: "2024-02-01",
    period_end: "2024-08-31",
    subject_status: null,
    created_at: "2026-10-02T09:00:00Z",
    updated_at: "2026-10-02T09:00:00Z",
  };
}

/** Signed in, with one case open under the given pack run (as the named expert unless told otherwise). */
export function inCase(run: PackRun, actions: string[] = EXPERT_ACTIONS) {
  signedIn({ me: me(), pack: casePack(run) });
  server.use(
    http.get("/v1/cases/:caseId", () => json(caseItem(run, actions))),
    http.get("/v1/cases/:caseId/questions", () => json(questions())),
  );
}

export function questions(): Schemas["Questions"] {
  const q = (id: string, number: number, text: string) =>
    ({ id, number, text_verbatim: text, confirmed_text: null, kind: "x", outside_expertise: false, para_ref: null, status: "confirmed", version: 1, source_word_ids: [], instruction_version_id: "b1111111-1111-4111-8111-111111111111" }) as unknown as Schemas["QuestionOut"];
  return {
    questions: [q(Q_1, 1, "Was the result acted on?"), q(Q_2, 2, "Should a referral have been made?")],
    assumptions: [],
    locked: false,
    reading: false,
    reopened: false,
    rejected: 0,
    warnings: [],
    version_no: 1,
    instruction_version_id: "b1111111-1111-4111-8111-111111111111",
  } as unknown as Schemas["Questions"];
}

export function event(over: Partial<Schemas["Event"]> = {}): Schemas["Event"] {
  return {
    fact_id: FACT_1,
    kind: "consultation",
    page_no: 118,
    event_date: "2024-05-15",
    available_at: "2024-05-15",
    status: "verified",
    question_ids: [],
    has_finding: false,
    ...over,
  };
}

export function timeline(over: Partial<Schemas["Timeline"]> = {}): Schemas["Timeline"] {
  return {
    lanes: [
      { key: "lane_one", label: "Lane one words", events: [event()] },
      { key: "lane_two", label: "Lane two words", events: [event({ fact_id: FACT_2, kind: "result", page_no: 214, event_date: "2024-02-19", status: "suggested", question_ids: [Q_1, Q_2], has_finding: true })] },
    ],
    unplaced: [],
    guidance_changed: [],
    ...over,
  };
}

export function entry(over: Partial<Schemas["Entry"]> = {}): Schemas["Entry"] {
  return { fact_id: FACT_2, kind: "result", page_no: 117, event_date: "2024-05-14", available_at: "2024-05-14", status: "verified", ...over };
}

export function consultation(over: Partial<Schemas["Consultation"]> = {}): Schemas["Consultation"] {
  return {
    title: "Seen on the day",
    entry: entry({ fact_id: FACT_1, kind: "consultation", page_no: 118, event_date: "2024-05-15", available_at: "2024-05-15" }),
    visible: [entry()],
    later_count: 1,
    later: [entry({ fact_id: FACT_3, kind: "letter", page_no: 301, event_date: "2024-05-10", available_at: "2024-05-22" })],
    unknown: [entry({ fact_id: "f4444444-4444-4444-8444-444444444444", kind: "referral", page_no: 150, event_date: "2024-05-01", available_at: null })],
    guidance: [{ code: "G1", title: "Guidance title", version_label: "2023 edition", url: "https://example.test/g1" }],
    guidance_changed: [],
    on: "2024-05-15",
    note: null,
    ...over,
  };
}

export function finding(over: Partial<Schemas["FindingOut"]> = {}): Schemas["FindingOut"] {
  return {
    id: "c1111111-1111-4111-8111-111111111111",
    check_key: "abnormal_result_no_action",
    rule: "no_action_within",
    gentle: false,
    evidence_fact_ids: [FACT_2],
    evidence_pages: [214],
    decision: null,
    reason: null,
    version: 1,
    status: "open",
    ...over,
  };
}

export const LINK_1 = "b2111111-1111-4111-8111-111111111111";

export function link(over: Partial<Schemas["LinkOut"]> = {}): Schemas["LinkOut"] {
  return {
    id: LINK_1,
    fact_id: FACT_2,
    question_id: Q_1,
    source: "ai",
    status: "suggested",
    reason: "same matter",
    version: 1,
    kind: "result",
    page_no: 214,
    event_date: "2024-02-19",
    ...over,
  };
}

export function comment(over: Partial<Schemas["CommentOut"]> = {}): Schemas["CommentOut"] {
  return {
    id: "d1111111-1111-4111-8111-111111111111",
    target_kind: "fact",
    target_id: FACT_1,
    body: "Ask about the earlier visit",
    author_id: PERSON_ID,
    mine: true,
    resolved_at: null,
    created_at: "2026-10-02T09:00:00Z",
    version: 1,
    ...over,
  };
}
