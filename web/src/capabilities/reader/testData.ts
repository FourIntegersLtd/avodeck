// Shared test data for slice B's screen tests (imported only by tests). Synthetic only: no real
// names, dates of birth or identifiers. Shapes follow the generated OpenAPI schema.
import type { Schemas } from "../../foundation/platform-client";

export const CASE_ID = "44444444-4444-4444-8444-444444444444";
export const pageId = (n: number) => `99999999-9999-4999-8999-${String(n).padStart(12, "0")}`;
export const factId = (n: number) => `88888888-8888-4888-8888-${String(n).padStart(12, "0")}`;
export const EXPERT_ACTIONS = ["case.view_metadata", "case.view_content", "fact.verify", "page.read"];

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

/** A 1x1 JPEG. */
export const JPEG = Uint8Array.from(
  atob("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="),
  (c) => c.charCodeAt(0),
);

export function pageDetail(n: number, over: Partial<Schemas["PageDetail"]> = {}): Schemas["PageDetail"] {
  return {
    page_id: pageId(n),
    page_no: n,
    source_type: "primary_care_record",
    read: false,
    in_period: true,
    unreadable: false,
    rotation: 0,
    relevance: null,
    needs_clearer_copy: false,
    superseded_by_page_no: null,
    duplicate_of_page_no: null,
    handwriting: null,
    has_transcription: false,
    first_read_at: null,
    ...over,
  };
}

export function fact(n: number, over: Partial<Schemas["FactOut"]> = {}): Schemas["FactOut"] {
  return {
    id: factId(n),
    kind: "consultation",
    fields: { date: "2024-05-15" },
    value: n === 1 ? "Dr P consultation 15 May 2024" : "Anusol HC suppositories",
    word_ids: [`p118w${n}a`, `p118w${n}b`],
    bbox: n === 1 ? { x0: 0.06, y0: 0.2, x1: 0.56, y1: 0.24 } : { x0: 0.1, y0: 0.4, x1: 0.5, y1: 0.43 },
    event_date: "2024-05-15",
    status: "suggested",
    version: 1,
    flagged: false,
    needs_recheck: false,
    ...over,
  };
}

export function queuePage(n: number, over: Partial<Schemas["UnreadablePage"]> = {}): Schemas["UnreadablePage"] {
  return {
    page_id: pageId(n),
    page_no: n,
    source_type: "hospital",
    handwriting: true,
    relevance: null,
    has_transcription: false,
    needs_clearer_copy: false,
    done: false,
    ...over,
  };
}
