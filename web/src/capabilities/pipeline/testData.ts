// Shared test data for slice A's screen tests (imported only by *.test.tsx). Synthetic only: no
// real names, dates of birth or identifiers. Shapes follow the generated OpenAPI schema.
import type { Schemas } from "../../foundation/platform-client";

export const CASE_ID = "44444444-4444-4444-8444-444444444444";
export const JOB = "77777777-7777-4777-8777-777777777777";
export const NEXT_JOB = "77777777-7777-4777-8777-777777777778";

export const EXPERT_ACTIONS = [
  "case.view_metadata",
  "case.view_content",
  "case.upload",
  "fact.verify",
  "page.quarantine",
  "page.review_duplicate",
  "request.draft",
];
export const VIEWER_ACTIONS = ["case.view_metadata", "case.view_content"];

export function theCase(over: Partial<Schemas["Case"]> = {}): Schemas["Case"] {
  return {
    id: CASE_ID,
    organisation_id: "22222222-2222-4222-8222-222222222222",
    named_expert_id: "11111111-1111-4111-8111-111111111111",
    jurisdiction: "gb-eaw",
    case_type: "clin-neg-breach",
    specialty: "general-practice",
    expert_role: "single",
    label: "Mrs A",
    party_role: "other_side",
    instructing_party_type: "lawyer",
    court_track: null,
    due_date: "2026-11-30",
    status: "open",
    on_hold: false,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    allowed_actions: EXPERT_ACTIONS,
    ...over,
  } as Schemas["Case"];
}

export function processingJob(over: Partial<Schemas["ProcessingJob"]> = {}): Schemas["ProcessingJob"] {
  return { id: JOB, kind: "process_bundle", status: "running", step: "sort", pct: 48, eta_seconds: 220, error_code: null, ...over };
}

const page = (n: number) => `99999999-9999-4999-8999-${String(n).padStart(12, "0")}`;
export const pageId = page;

export interface TestDocument {
  id: string;
  kind: string;
  filename: string;
  pages: number | null;
  first_page_no: number | null;
  last_page_no: number | null;
  received_on: string | null;
  obtained_how: string | null;
  bundle_version: number;
}

export function documents(): TestDocument[] {
  return [
    { id: "d1000000-0000-4000-8000-000000000001", kind: "original", filename: "practice-records.pdf", pages: 600, first_page_no: 1, last_page_no: 600, received_on: "2026-10-02", obtained_how: "Subject access request", bundle_version: 1 },
    { id: "d1000000-0000-4000-8000-000000000002", kind: "original", filename: "hospital-notes.pdf", pages: 631, first_page_no: 601, last_page_no: 1231, received_on: "2026-10-02", obtained_how: null, bundle_version: 1 },
  ];
}

/** A summary whose pages are split across the pack's source types. */
export function summary(pack: Schemas["CasePack"], over: Partial<Schemas["BundleSummary"]> = {}): Schemas["BundleSummary"] {
  const types = pack.source_types as Array<{ key: string }>;
  const each = Math.floor(1231 / types.length);
  return {
    pages: 1231,
    by_source: types.map((s, i) => ({ source_type: s.key, pages: i === 0 ? 1231 - each * (types.length - 1) : each })),
    duplicates: 37,
    unreadable: 31,
    straightened: 0,
    gaps: 1,
    missing_items: 2,
    documents: documents() as unknown as Schemas["BundleSummary"]["documents"],
    versions: [{ bundle_version: 1, documents: 2, pages: 1231, first_page_no: 1, last_page_no: 1231, added_at: "2026-10-02T09:00:00Z" }],
    quarantine_suggestions: [412, 413, 414, 415].map((n) => ({ page_id: page(n), page_no: n, status: "suggested", reason: "identifiers_differ", job_id: JOB })),
    quarantined: [],
    exhibits: [{ id: "e1000000-0000-4000-8000-000000000001", document_id: "d1000000-0000-4000-8000-000000000003", filename: "call.m4a", media_type: "audio/mp4", size: 1024, part_of: null, added_at: "2026-10-02T09:00:00Z" }],
    foreign_language: [{ page_id: page(903), page_no: 903, language: "cy" }],
    ...over,
  } as Schemas["BundleSummary"];
}

export function missingItem(over: Partial<Schemas["MissingItem"]> = {}): Schemas["MissingItem"] {
  return {
    id: "m1000000-0000-4000-8000-000000000001",
    citing_page_id: page(1118),
    citing_page_no: 1118,
    word_ids: ["w1", "w2"],
    description: "Colonoscopy report from September",
    reference_date: "2024-09-24",
    status: "open",
    reason: null,
    received_page_id: null,
    received_page_no: null,
    version: 1,
    ...over,
  } as Schemas["MissingItem"];
}

export function duplicate(over: Partial<Schemas["DuplicatePage"]> = {}): Schemas["DuplicatePage"] {
  return { page_id: page(20), page_no: 20, copy_of_page_id: page(3), copy_of_page_no: 3, status: "suggested", version: 1, ...over };
}

export function draft(over: Partial<Schemas["RequestDraft"]> = {}): Schemas["RequestDraft"] {
  return {
    label: "Ask",
    subject: "Records needed - Mrs A",
    body: "Please supply:\n- Colonoscopy report from September\n\nLegible copies needed:\n- page 612",
    missing_item_ids: ["m1000000-0000-4000-8000-000000000001"],
    clearer_copy_page_ids: [page(612)],
    clearer_copy_page_nos: [612],
    translation_page_ids: [],
    translation_page_nos: [],
    unreadable_document_ids: [],
    unreadable_document_names: [],
    ...over,
  };
}

/** A tiny JPEG-ish payload for thumbnail responses. */
export const THUMB = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 0xff, 0xd9]);

/** The pack's words, as the screens read them. */
export function packWords(pack: Schemas["CasePack"]) {
  const vocab = pack.vocabulary as Record<string, unknown>;
  const request = vocab.instructing_party_request as { label: string };
  const sources = (vocab.source_types ?? {}) as Record<string, string>;
  const party = (pack.instructing_party_types as Array<{ key: string; label: string }>)[0]!;
  return { askLabel: request.label, sources, party };
}
