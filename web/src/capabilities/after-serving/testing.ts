// Test stand-ins for slice G's API: typed bodies built from the generated schema, with every pack word
// (kinds, party names, declaration wording, written-question name) taken from the pack fixtures.
import { HttpResponse } from "msw";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server } from "../../test/api";
import { casePack, inCase, type PackRun } from "../setup/testing";

export { casePack, inCase };
export type { PackRun };

export const V1 = "b1111111-1111-4111-8111-111111111111";
export const V2 = "b2222222-2222-4222-8222-222222222222";
export const P1 = "e1111111-1111-4111-8111-111111111111";
export const P2 = "e2222222-2222-4222-8222-222222222222";
export const ROUND = "a1111111-1111-4111-8111-111111111111";
export const Q1 = "a2222222-2222-4222-8222-222222222222";
export const Q2 = "a3333333-3333-4333-8333-333333333333";
export const D1 = "d1111111-1111-4111-8111-111111111111";
export const D2 = "d2222222-2222-4222-8222-222222222222";
export const DOC = "c1111111-1111-4111-8111-111111111111";
export const CLAIM = "c2222222-2222-4222-8222-222222222222";
export const CHANGE = "c3333333-3333-4333-8333-333333333333";

type Kind = { key: string; set_by?: string };
export const deadlineKinds = (run: PackRun) => (casePack(run).deadline_rules as unknown as Kind[]).map((r) => r.key);
export const firstKind = (run: PackRun) => deadlineKinds(run)[0]!;
export const declarations = (run: PackRun) => (casePack(run) as unknown as { declarations: Array<{ key: string; text: string }> }).declarations;
export const questionsName = (run: PackRun) => String((casePack(run).vocabulary as Record<string, unknown>).written_questions);
export const partyName = (run: PackRun) => (casePack(run).instructing_party_types as Array<{ key: string; label?: string }>)[0]!.label!;
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export const deadline = (over: Partial<Schemas["DeadlineOut"]> = {}): Schemas["DeadlineOut"] => ({
  id: D1, kind: "written_questions", due_on: "2026-12-26", due_at: "2026-12-26T23:59:59Z", timezone: "Europe/London", calendar: "calendar_days",
  created_from: "pack", source: "pack", rule_ref: "ref", pack_version: "1", hearing_id: null, done_at: null, last_reminded_on: null, version: 1, ...over,
});

export const served = (over: Partial<Schemas["Served"]> = {}): Schemas["Served"] => ({
  version: { id: V1, display_name: "Report 1", status: "served", version_no: 1, served_on: "2026-11-28", sha256: "abc" },
  paragraphs: [
    { id: P1, para_no: 614, text: "Mrs A should have been referred in early March 2024.", fact_ids: [] },
    { id: P2, para_no: 73, text: "In summary, referral was late.", fact_ids: [] },
  ],
  ...over,
});

export const round = (over: Partial<Schemas["RoundOut"]> = {}): Schemas["RoundOut"] => ({
  id: ROUND, number: 1, from_party_id: null, received_on: "2026-12-01", document_id: DOC, deadline_id: D1, answers_version_id: null,
  status: "open", extracted_at: "2026-12-01T10:00:00Z", version: 1, ...over,
});

export const question = (over: Partial<Schemas["WrittenQuestionOut"]> = {}): Schemas["WrittenQuestionOut"] => ({
  id: Q1, round_id: ROUND, number: 1, para_ref: "6.12 to 6.14", rule_ref: "ref", text_verbatim: "Would your opinion change if the examination was not recorded?",
  status: "received", suggested_classification: "clarifies", classification_reason: "refers to a paragraph", classification: null,
  suggested_paragraph_ids: [P1], paragraph_ids: [], answer_text: null, citation_refs: [], told_instructing_party_at: null, version: 1, ...over,
});

export const claim = (over: Partial<Schemas["OpposingClaimOut"]> = {}): Schemas["OpposingClaimOut"] => ({
  id: CLAIM, claim_no: 1, text_verbatim: "Haemoglobin was 89 in February", paragraph_ref: "4.2", their_page_ref: "214", their_quote: "89", our_page_no: 214, our_quote: "98",
  status: "wrong_value", reason: "number differs", checked_by: "code", discrepancy: true, expert_confirmed: false, confirmed_at: null, version: 1, ...over,
}) as Schemas["OpposingClaimOut"];

export const change = (over: Partial<Schemas["ReportChangeOut"]> = {}): Schemas["ReportChangeOut"] => ({
  id: CHANGE, paragraph_id: P1, kind: "change_of_opinion", old_text: "Referred in early March.", new_text: "Referred by 15 May at the latest.", reason: "Having considered their report.",
  citation_refs: [], dependent_paragraph_ids: [], suggested_dependents: [], supplementary_version_id: null, version: 1, ...over,
});

export type Sent = { method: string; path: string; body: unknown; ifMatch: string | null };

export function etagged(body: unknown, version: number, status = 200) {
  return HttpResponse.json(body as never, { status, headers: { etag: `"${version}"` } });
}

/** Signed in under a pack, with a stub for every slice G read; `record` notes what a handler was sent. */
export function serve(run: PackRun, opts: {
  deadlines?: Schemas["DeadlineOut"][]; served?: Schemas["Served"]; hearings?: Schemas["HearingOut"][]; directions?: Schemas["DirectionOut"][]; steps?: Schemas["AfterServingSteps"];
  rounds?: Schemas["RoundOut"][]; questions?: Schemas["WrittenQuestionOut"][]; pages?: Schemas["NewPage"][]; opposing?: Schemas["OpposingReport"] | null;
  changes?: Schemas["ReportChangeOut"][]; supplementary?: Schemas["Supplementary"] | null; deadlineChanges?: Record<string, unknown[]>;
} = {}) {
  inCase(run, { instructing_party_type: (casePack(run).instructing_party_types as Array<{ key: string }>)[0]!.key });
  const sent: Sent[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    sent.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null, ifMatch: request.headers.get("if-match") });
  };
  server.use(
    http.get("/v1/cases/:caseId/deadlines", () => json({ deadlines: opts.deadlines ?? [deadline()], served_on: "2026-11-28" })),
    http.get("/v1/cases/:caseId/deadlines/:id/changes", ({ params }) => json({ changes: opts.deadlineChanges?.[String(params.id)] ?? [] } as never)),
    http.get("/v1/cases/:caseId/report/served", () => json((opts.served ?? served()) as never)),
    http.get("/v1/cases/:caseId/hearings", () => json({ hearings: opts.hearings ?? [] })),
    http.get("/v1/cases/:caseId/after-serving/steps", () => json((opts.steps ?? { meetings: [], pack: null }) as never)),
    http.get("/v1/cases/:caseId/court-directions", () => json({ directions: opts.directions ?? [] })),
    http.get("/v1/cases/:caseId/written-question-rounds", () => json({ rounds: opts.rounds ?? [round()] })),
    http.get("/v1/cases/:caseId/written-questions", () => json({ questions: opts.questions ?? [question()] })),
    http.get("/v1/cases/:caseId/new-since-served", () => json({ pages: opts.pages ?? [], served_at: "2026-11-28T10:00:00Z" })),
    http.get("/v1/cases/:caseId/opposing-reports/:doc", () =>
      opts.opposing ? json(opts.opposing as never) : HttpResponse.json({ error: { code: "not_found", message: "x" } }, { status: 404 })),
    http.get("/v1/cases/:caseId/report-changes", () => json({ changes: opts.changes ?? [], supplementary: opts.supplementary ?? null } as never)),
  );
  return { sent, record };
}
