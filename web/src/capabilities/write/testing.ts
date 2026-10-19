// Test stand-ins for slice E's API: typed bodies built from the generated schema, with the sides, views and
// prompts taken from the pack fixtures (never written here). Imported by the write tests only.
import { HttpResponse } from "msw";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server } from "../../test/api";
import { casePack, inCase, question, questions, type PackRun } from "../setup/testing";

export { casePack, inCase, question, questions };
export type { PackRun };

export const Q1 = "a1111111-1111-4111-8111-111111111111";
export const Q2 = "a2222222-2222-4222-8222-222222222222";
export const FACT_1 = "f1111111-1111-4111-8111-111111111111";
export const FACT_2 = "f2222222-2222-4222-8222-222222222222";
export const FACT_3 = "f3333333-3333-4333-8333-333333333333";
export const PAGE_1 = "91111111-1111-4111-8111-111111111111";
export const PAGE_2 = "92222222-2222-4222-8222-222222222222";

export function sides(run: PackRun): Schemas["Side"][] {
  return (casePack(run) as unknown as { point_sides: Schemas["Side"][] }).point_sides.map((s) => ({ code: s.code, label: s.label }));
}
export function views(run: PackRun): Schemas["OpinionView"][] {
  return (casePack(run) as unknown as { opinion_views: Schemas["OpinionView"][] }).opinion_views.map((s) => ({ code: s.code, label: s.label }));
}

export function pointFact(over: Partial<Schemas["PointFact"]> = {}): Schemas["PointFact"] {
  return { id: FACT_1, page_id: PAGE_1, page_no: 118, kind: "symptom", value: "Bleeding for 3 weeks", event_date: "2024-05-15", ...over };
}

export function chip(over: Partial<Schemas["Chip"]> = {}): Schemas["Chip"] {
  return { fact_id: FACT_1, page_id: PAGE_1, page_no: 118, kind: "symptom", value: "Bleeding for 3 weeks", event_date: "2024-05-15", quote_verbatim: "bleeding for three weeks", fact_version: 2, ...over };
}

export function pointOut(over: Partial<Schemas["PointOut"]> = {}): Schemas["PointOut"] {
  return { id: "b1111111-1111-4111-8111-111111111111", side: "supports_claim", status: "suggested", origin: "ai", reason: null, dismissed_reason: null, flagged: false, version: 1, fact: pointFact(), ...over };
}

export function pointsBody(run: PackRun, over: Partial<Schemas["Points"]> = {}): Schemas["Points"] {
  return {
    sides: sides(run),
    points: [pointOut()],
    unused: [pointFact({ id: FACT_2, page_id: PAGE_2, page_no: 214, value: "Iron deficiency on file" })],
    relevant: [],
    claims: [],
    range_note: { prompt: (casePack(run) as unknown as { range_note_prompt: string }).range_note_prompt, text: null, version: 0, dictated_on: null },
    question: header(run),
    ...over,
  };
}

export function header(run: PackRun, over: Partial<Schemas["QuestionHeader"]> = {}): Schemas["QuestionHeader"] {
  const first = casePack(run).question_types[0]!;
  return { number: 1, short_title: "February bloods", kind: String(first.key), kind_label_key: String(first.label_key), para_ref: null, ...over };
}

export function claim(over: Partial<Schemas["ClaimOut"]> = {}): Schemas["ClaimOut"] {
  return {
    id: "c1111111-1111-4111-8111-111111111111", document_id: "d1111111-1111-4111-8111-111111111111", claim_index: 0,
    text_verbatim: "she told the doctor she had lost weight", verdict: "contradicts", expert_verdict: null, status: "suggested",
    version: 1, word_ids: ["p1-w1"], facts: [pointFact({ id: FACT_3, page_no: 300, value: "Weight loss first mentioned on 8 August" })], ...over,
  };
}

export function opinionOut(over: Partial<Schemas["OpinionOut"]> = {}): Schemas["OpinionOut"] {
  return {
    id: "e1111111-1111-4111-8111-111111111111", question_id: Q1, body: "", citation_refs: [], view: null, provisional: false,
    test_text: null, restored_from: null, version: 1, updated_at: "2026-10-18T09:00:00Z", lint: [], changed_citations: [], stale: false, ...over,
  };
}

export function workspace(run: PackRun, over: Partial<Schemas["Workspace"]> = {}): Schemas["Workspace"] {
  return {
    question_id: Q1,
    chips: [chip(), chip({ fact_id: FACT_2, page_id: PAGE_2, page_no: 214, value: "Iron deficiency on file", quote_verbatim: "iron deficiency anaemia" })],
    guidance_on: "2024-05-15",
    guidance: [{ code: "g1", title: "Guidance one", source: "Source one", url: null, in_force_from: "2023-08-01", in_force_to: null }] as Schemas["WorkspaceGuidance"][],
    points: { sides: sides(run).map((s, i) => ({ ...s, count: i + 2 })), differ: 1 },
    views: views(run),
    legal_test: "The pack's own test of what a reasonable practitioner does.",
    opinion: null,
    question: header(run),
    ...over,
  };
}

export function lintIssue(over: Partial<Schemas["LintIssueOut"]> = {}): Schemas["LintIssueOut"] {
  return { id: "11111111-aaaa-4aaa-8aaa-111111111111", sentence_index: 0, sentence_start: 0, sentence_end: 14, source: "regex", status: "open", type: "uncited_fact", words: "she had a lump", ...over };
}

export function progress(over: Partial<Schemas["WriteProgress"]> = {}): Schemas["WriteProgress"] {
  return {
    answered: 1,
    total: 2,
    questions: [
      { question_id: Q1, number: 1, short_title: "February bloods", points_done: true, has_opinion: true },
      { question_id: Q2, number: 2, short_title: "15 May visit", points_done: false, has_opinion: false },
    ],
    ...over,
  };
}

export type Sent = { method: string; path: string; body: unknown; ifMatch: string | null };

/** Signed in under a pack, with two answerable questions and a stub for each slice E endpoint. */
export function serve(run: PackRun, opts: { points?: Schemas["Points"]; workspace?: Schemas["Workspace"]; progress?: Schemas["WriteProgress"] } = {}) {
  inCase(run);
  const sent: Sent[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    sent.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null, ifMatch: request.headers.get("if-match") });
  };
  const kind = String(casePack(run).question_types[0]!.key);
  server.use(
    http.get("/v1/cases/:caseId/questions", () =>
      json(questions({ questions: [question({ id: Q1, number: 1, kind, status: "confirmed" }), question({ id: Q2, number: 2, kind, status: "edited", text_verbatim: "Second question." }), question({ id: "a3333333-3333-4333-8333-333333333333", number: 3, status: "suggested" })] })),
    ),
    http.get("/v1/cases/:caseId/questions/:q/points", () => json(opts.points ?? pointsBody(run))),
    http.get("/v1/cases/:caseId/write-progress", () => json(opts.progress ?? progress())),
    http.get("/v1/cases/:caseId/questions/:q/workspace", () => json(opts.workspace ?? workspace(run))),
  );
  return { sent, record };
}

export function ok(body: unknown, version: number, status = 200) {
  return HttpResponse.json(body as never, { status, headers: { etag: `"${version}"` } });
}
