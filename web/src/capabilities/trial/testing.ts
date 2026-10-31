// Test stand-ins for slice I's API: typed bodies from the generated schema. Pack words (the court bundle's
// name, the meeting document) come from the pack fixtures, never written here.
import type { CopyContent } from "./copy";
import { HttpResponse } from "msw";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { casePack, inCase, launchWords, type PackRun } from "../setup/testing";

export { CASE_A, casePack, inCase, launchWords };
export type { PackRun };

export const MEETING = "a9111111-1111-4111-8111-111111111111";
export const ITEMS = ["a9222222-2222-4222-8222-222222222221", "a9222222-2222-4222-8222-222222222222", "a9222222-2222-4222-8222-222222222223"];
export const BUNDLE = "a9333333-3333-4333-8333-333333333333";
export const ROWS = ["a9444444-4444-4444-8444-444444444441", "a9444444-4444-4444-8444-444444444442", "a9444444-4444-4444-8444-444444444443"];
export const PACK = "a9555555-5555-4555-8555-555555555555";
export const COPY = "a9666666-6666-4666-8666-666666666666";

/** What the named expert may do on the case (docs/spec/permissions.md), as GET /cases/{id} returns it. */
export const EXPERT_ACTIONS = [
  "case.view_metadata", "case.view_content", "case.edit_setup", "case.upload", "opinion.write", "fact.verify",
  "report.build", "report.sign", "request.draft", "case.export",
];
const expertCase = (run: PackRun) => inCase(run, { allowed_actions: EXPERT_ACTIONS });

/** A word from the case pack's vocabulary (for example `court_bundle`). */
export const term = (run: PackRun, key: string) => String((casePack(run).vocabulary as Record<string, unknown>)[key]);

export const MEETING_TYPE: Schemas["MeetingTypeOut"] = {
  key: "zz_conference",
  label: "Zz conference minute",
  declarations: ["Zz we affirm our duty to the court.", "Zz no one asked us to avoid agreeing."],
  notes: "Zz the parties do not see the draft.",
};

export function item(n: 1 | 2 | 3, over: Partial<Schemas["ItemOut"]> = {}): Schemas["ItemOut"] {
  const base: Schemas["ItemOut"][] = [
    {
      id: ITEMS[0]!, number: 1, text: "Did Dr P need to examine Mrs A on 15 May 2024?",
      own_position: "Yes. Bleeding with known anaemia called for an examination.", other_positions: "Yes, an examination was required.",
      agreed: true, reason: null, change_note: null, deviation: "consistent", deviation_reason: "same_words", served_paragraphs: [13], served_text: "Bleeding with known anaemia called for an examination [p.118].", pages: [118],
      changed_from_previous: "same", version: 3,
    },
    {
      id: ITEMS[1]!, number: 2, text: "Was the February result acted on appropriately?",
      own_position: "No. Iron was started without looking for a cause.", other_positions: "Iron first, review later, was reasonable.",
      agreed: false, reason: "We differ on whether a cause should have been sought at once.", change_note: null, deviation: "consistent",
      deviation_reason: "copied_from_report", served_paragraphs: [4], served_text: "Iron was started on 19 February 2024 [p.103].", pages: [214, 87], changed_from_previous: "same", version: 2,
    },
    {
      id: ITEMS[2]!, number: 3, text: "By what date should Mrs A have been referred?",
      own_position: "By 15 May 2024 at the latest.", other_positions: "By 15 May 2024.", agreed: true, reason: null, change_note: null,
      deviation: "changed", deviation_reason: "differs_from_report", served_paragraphs: [14], served_text: "Zz early March 2024, soon after the blood result [p.118].", pages: [118], changed_from_previous: "changed", version: 5,
    },
  ];
  return { ...base[n - 1]!, ...over };
}

export function meetingOut(over: Partial<Schemas["MeetingOut"]> = {}): Schemas["MeetingOut"] {
  return {
    id: MEETING, type: MEETING_TYPE.key, label: MEETING_TYPE.label, status: "draft", hearing_id: null, discussion_on: "2027-02-12",
    sign_by: "2027-02-19", sign_rule_ref: "Rule Z.9", sign_rule_phrase: "Zz 5 working days after the conference", send_by: "2027-02-26", send_rule_ref: "Rule Z.10", send_rule_phrase: "Zz within 10 working days",
    declarations: MEETING_TYPE.declarations, prepared_sha256: null, prepared_pages: null, certificate_sha256: null, signed_sha256: null,
    version: 4, items: [item(1), item(2), item(3)],
    agenda_versions: [{ id: "a9777777-7777-4777-8777-777777777777", version_no: 2, read_at: "2027-02-10T09:00:00Z", item_count: 3, created_at: "2027-02-10T09:00:00Z" }],
    ...over,
  };
}

export const participants: Schemas["ParticipantOut"][] = [
  { id: "a9888888-8888-4888-8888-888888888888", name: "Dr R", discipline: null, side: "other_side", on_avodeck: false },
];

export function bundleOut(over: Partial<Schemas["BundleOut"]> = {}): Schemas["BundleOut"] {
  return { id: BUNDLE, status: "matched", hearing_id: null, received_on: "2027-06-02", page_count: 3914, version: 2, ...over };
}

export function rows(): Schemas["RowOut"][] {
  return [
    { id: ROWS[0]!, page_no: 118, trial_page_no: 2407, status: "matched", method: "text", score: 0.97, confirmed: false, paragraphs: [13], version: 1 },
    { id: ROWS[1]!, page_no: 103, trial_page_no: 2390, status: "to_confirm", method: "sequence", score: 0.71, confirmed: false, paragraphs: [12], version: 2 },
    { id: ROWS[2]!, page_no: 214, trial_page_no: null, status: "missing", method: "none", score: 0, confirmed: false, paragraphs: [11, 4], version: 1 },
  ];
}

export function concordanceOut(over: Partial<Schemas["ConcordanceOut"]> = {}): Schemas["ConcordanceOut"] {
  const base = { bundle: bundleOut(), rows: rows(), counts: { matched: 1, to_confirm: 1, missing: 1 }, ...over };
  // Stand-in for the server's answers: the missing rows, and the rows in display order with a hidden count.
  let room = 4;
  const display_rows = base.rows.filter((r) => (r.status === "to_confirm" && !r.confirmed) || room-- > 0);
  const missing_rows = base.rows.filter((r) => r.status === "missing");
  return { missing_count: missing_rows.length, missing_rows, display_rows, hidden: base.rows.length - display_rows.length, ...base, ...over };
}

export const ALL_SECTIONS = ["report", "chronology", "joint_statement", "written_answers", "legal_tests"] as const;

export function settingsOut(over: Partial<Schemas["TrialPackSettingsOut"]> = {}): Schemas["TrialPackSettingsOut"] {
  return { sections: ["report", "chronology"], include_guidance: false, hearing_id: null, version: 2, ...over } as Schemas["TrialPackSettingsOut"];
}

export function packOut(over: Partial<Schemas["PackOut"]> = {}): Schemas["PackOut"] {
  return { id: PACK, status: "ready", sha256: "c".repeat(64), page_count: 54, sections: ["report", "chronology"], section_pages: [{ key: "report", pages: 31 }, { key: "chronology", pages: 9 }], stale: false, stale_reason: null, created_at: "2027-06-03T09:00:00Z", ...over };
}

export function previewOut(over: Partial<Schemas["PreviewOut"]> = {}): Schemas["PreviewOut"] {
  return {
    sections: [{ key: "report", pages: 31 }, { key: "chronology", pages: 9 }, { key: "readiness", pages: 2 }, { key: "joint_statement", pages: 6 }],
    total_pages: 48,
    case_label: "Mrs A",
    sheets: [
      {
        number: 1, text: "Did Dr P need to examine Mrs A on 15 May 2024?", answer: "Yes. Bleeding with known anaemia called for an examination.",
        asked: "Yes, an examination was required.", agreed: true, reason: null, pages: [{ page_no: 118, trial_page_no: 2407, status: "matched" }], pack_page_no: 40,
      },
      {
        number: 2, text: "Was the February result acted on appropriately?", answer: "No. Iron was started without looking for a cause.",
        asked: "Iron first, review later, was reasonable.", agreed: false, reason: "We differ on whether a cause should have been sought.",
        pages: [{ page_no: 214, trial_page_no: null, status: "missing" }, { page_no: 87, trial_page_no: 2376, status: "matched" }], pack_page_no: 41,
      },
    ],
    ...over,
  };
}

export function copyOut(over: Partial<Schemas["PackageOut"]> = {}): Schemas["PackageOut"] {
  return {
    id: COPY, device_label: "My tablet", hearing_id: null, format_version: 1, include_images: false, sha256: "d".repeat(64), status: "built",
    expires_at: "2099-07-01T09:00:00Z", downloaded_at: null, revoked_at: null, created_at: "2027-06-03T09:00:00Z", stale: false, stale_reason: null, ...over,
  };
}

export type Sent = { method: string; path: string; search: string; body: unknown; ifMatch: string | null; contentType: string | null };

/** Records what the screen sent, so tests check the request rather than the screen's internals. */
export function recorder() {
  const sent: Sent[] = [];
  const record = async (request: Request) => {
    const type = request.headers.get("content-type");
    const url = new URL(request.url);
    let body: unknown = null;
    if (type?.includes("json")) body = JSON.parse(await request.text());
    else if (request.method !== "GET" && request.method !== "DELETE") body = (await request.arrayBuffer()).byteLength;
    sent.push({ method: request.method, path: url.pathname, search: url.search, body, ifMatch: request.headers.get("if-match"), contentType: type });
  };
  return { sent, record };
}

export const pdf = (name = "file.pdf") => new File([new Uint8Array([37, 80, 68, 70, 45, 49])], name, { type: "application/pdf" });

export const blobReply = (type: string) => new HttpResponse(new Blob(["%PDF-1"], { type }), { headers: { "content-type": type } });

export const etagged = (body: unknown, version: number) => HttpResponse.json(body as never, { headers: { ETag: `"${version}"` } });

/** The joint statement screen's API, with one meeting unless `meeting` is null. */
export function serveMeeting(run: PackRun, opts: { meeting?: Schemas["MeetingOut"] | null } = {}) {
  expertCase(run);
  const { sent, record } = recorder();
  const meeting = opts.meeting === undefined ? meetingOut() : opts.meeting;
  server.use(
    http.get("/v1/cases/:caseId/expert-meetings/types", () => json({ types: [MEETING_TYPE] })),
    http.get("/v1/cases/:caseId/expert-meetings", () => json({ meetings: meeting ? [meeting] : [] })),
    http.get("/v1/cases/:caseId/expert-meetings/:meetingId", () => (meeting ? etagged(meeting, meeting.version) : json({ error: { code: "not_found", message: "x" } }, 404))),
    http.get("/v1/cases/:caseId/expert-meetings/:meetingId/participants", () => json({ participants })),
  );
  return { sent, record };
}

export function serveConcordance(run: PackRun, body: Schemas["ConcordanceOut"] = concordanceOut()) {
  expertCase(run);
  const { sent, record } = recorder();
  server.use(http.get("/v1/cases/:caseId/concordance", () => json(body)));
  return { sent, record };
}

export function servePack(run: PackRun, opts: { latest?: Schemas["LatestOut"]; copies?: Schemas["PackageOut"][]; preview?: Schemas["PreviewOut"] } = {}) {
  expertCase(run);
  const { sent, record } = recorder();
  server.use(
    http.get("/v1/cases/:caseId/trial-pack/settings", () => etagged(settingsOut(), 2)),
    http.get("/v1/cases/:caseId/trial-pack/latest", () => json(opts.latest ?? { pack: packOut(), settings: settingsOut() })),
    http.get("/v1/cases/:caseId/trial-pack/preview", () => json(opts.preview ?? previewOut())),
    http.get("/v1/cases/:caseId/offline-packages", () => json({ packages: opts.copies ?? [], live: (opts.copies ?? []).filter((c) => c.status === "built" || c.status === "downloaded") })),
  );
  return { sent, record };
}

export const CASE = CASE_A;

/** Format version 1 of the offline copy, as the API builds it (routes_offline `content()`). */
export function copyContent(over: Partial<CopyContent> = {}): CopyContent {
  return {
    format_version: 1, case_id: CASE_A, built_at: "2027-06-03T09:00:00Z", date_order: "DMY", court_bundle: "Zz binder",
    report: {
      version_no: 1, served_on: "2027-01-10",
      paragraphs: [
        { para_no: 13, section: "opinion", text: "Bleeding with known anaemia called for an examination [p.118].", pages: [{ page_no: 118, trial_page_no: 2407 }] },
        { para_no: 4, section: "facts", text: "Iron was started on 19 February 2024 [p.103].", pages: [{ page_no: 103, trial_page_no: null }] },
      ],
    },
    facts: [
      { kind: "consultation", text: "Bleeding reported, no examination", date: "2024-05-15", page_no: 118, trial_page_no: 2407, source_type: "primary_care_record" },
      { kind: "prescription", text: "Ferrous sulphate 200 mg", date: "2024-02-19", page_no: 103, trial_page_no: null, source_type: "hospital" },
    ],
    case_label: "Mrs A",
    source_types: [{ key: "primary_care_record", label: "Zz record", colour: "#1E6B4E" }, { key: "hospital", label: "Zz hospital", colour: "#4C6E91" }],
    concordance: [{ page_no: 118, trial_page_no: 2407, status: "matched" }, { page_no: 103, trial_page_no: null, status: "to_confirm" }],
    joint_statement: { type: MEETING_TYPE.key, status: "signed", items: [{ number: 1, text: item(1).text, own_position: item(1).own_position, agreed: true, reason: null }] },
    answers: [{ question_no: 2, text: "The haemoglobin was 98 on page 103." }],
    ...over,
  };
}

const b64 = (bytes: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

/** Seals a copy the way the API does (AES-GCM, AAD `avodeck-offline:{id}:{format}`), giving the one-time download body. */
export async function sealed(content: CopyContent = copyContent(), id = COPY): Promise<Schemas["Download"]> {
  const raw = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt"]);
  const additionalData = new TextEncoder().encode(`avodeck-offline:${id}:${content.format_version}`);
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData }, key, new TextEncoder().encode(JSON.stringify(content)));
  return { id, case_id: content.case_id, format_version: content.format_version, key: b64(raw), iv: b64(iv), ciphertext: b64(data) };
}

/** The PIN the expert chooses for the witness-box copy in these tests. */
export const PIN = "246810";

/** What the status check answers for the copy (screen 25 asks it when online): a status, an error, or no network. */
export function serveCopyStatus(answer: Schemas["PackageStatus"]["status"] | 401 | 404 | "no_network") {
  server.use(
    http.get("/v1/cases/:caseId/offline-packages/:packageId/status", () => {
      if (answer === "no_network") return HttpResponse.error();
      if (answer === 401) return apiError(401, "not_signed_in");
      if (answer === 404) return apiError(404, "not_found");
      return json({ status: answer });
    }),
  );
}
