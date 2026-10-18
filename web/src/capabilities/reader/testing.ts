// Test stand-in for the reader API (MSW). It keeps the server's blind-read rule: facts answer 423 until
// the page's read is recorded. Imported by the reader tests only.
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_ID, fact, JPEG, pageDetail, queuePage, theCase } from "./testData";

export interface ReaderApi {
  factRequests: number;
  reads: Array<{ page: number; body: { dwell_seconds: number } }>;
  verifies: Array<{ id: string; ifMatch: string | null; body: Record<string, unknown> }>;
  flags: Array<{ id: string; method: string }>;
  puts: Array<{ page: number; body: Record<string, unknown> }>;
  patches: Array<{ page: number; body: Record<string, unknown> }>;
  facts: Schemas["FactOut"][];
  read: boolean;
  verifyStatus?: number;
  verifyCode?: string;
  putStatus?: number;
  putCode?: string;
}

export interface ReaderOpts {
  page?: number;
  alreadyRead?: boolean;
  /** The page detail says read although the server holds no read record (to prove a 423 shows nothing). */
  pageSaysRead?: boolean;
  inPeriod?: boolean;
  facts?: Schemas["FactOut"][];
  queue?: Schemas["UnreadablePage"][];
}

/** Mounts every reader endpoint for a case; returns what it saw. */
export function mockReader(pack: Schemas["CasePack"], opts: ReaderOpts = {}): ReaderApi {
  const pageNo = opts.page ?? 118;
  const api: ReaderApi = {
    factRequests: 0,
    reads: [],
    verifies: [],
    flags: [],
    puts: [],
    patches: [],
    facts: opts.facts ?? [fact(1), fact(2)],
    read: opts.alreadyRead ?? false,
  };
  let queue = opts.queue ?? [queuePage(612), queuePage(640), queuePage(655)];
  const c = `/v1/cases/${CASE_ID}`;
  signedIn({ pack });
  server.use(
    http.get(c, () => json(theCase())),
    http.get(`${c}/pages/unreadable`, () => json({ pages: queue, done: queue.filter((p) => p.done).length, total: queue.length })),
    http.get(`${c}/pages`, () => json({ pages: [{ page_no: pageNo, page_id: "x", source_type: null, read: false, in_period: true, unreadable: false, duplicate_of_page_no: null }], next_cursor: null })),
    http.get(`${c}/progress`, () => json({ read: 3, total: 10, seconds_left: 300 })),
    http.get(`${c}/pages/:n/image`, () => new HttpResponse(JPEG, { headers: { "Content-Type": "image/jpeg" } })),
    http.get(`${c}/pages/:n/facts`, () => {
      api.factRequests += 1;
      return api.read ? json({ facts: api.facts }) : apiError(423, "blind_read");
    }),
    http.post(`${c}/pages/:n/read`, async ({ params, request }) => {
      api.reads.push({ page: Number(params.n), body: (await request.json()) as { dwell_seconds: number } });
      api.read = true;
      return json({ page_no: Number(params.n), read: true, first_read_at: "2026-10-18T10:00:00Z" });
    }),
    http.post(`${c}/facts/:id/verify`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.verifies.push({ id: String(params.id), ifMatch: request.headers.get("If-Match"), body });
      if (api.verifyStatus) return apiError(api.verifyStatus, api.verifyCode ?? "stale");
      const current = api.facts.find((f) => f.id === params.id)!;
      const status = body.action === "correct" ? "verified" : body.action === "not_right" ? "rejected" : "edited";
      const next = { ...current, status, version: current.version + 1, value: body.action === "edit" ? String(body.new_value) : current.value };
      api.facts = api.facts.map((f) => (f.id === next.id ? next : f));
      return json(next, 200);
    }),
    http.post(`${c}/facts/:id/flag-point`, ({ params }) => {
      api.flags.push({ id: String(params.id), method: "POST" });
      api.facts = api.facts.map((f) => (f.id === params.id ? { ...f, flagged: true } : f));
      return json({ fact_id: params.id, flagged: true });
    }),
    http.delete(`${c}/facts/:id/flag-point`, ({ params }) => {
      api.flags.push({ id: String(params.id), method: "DELETE" });
      api.facts = api.facts.map((f) => (f.id === params.id ? { ...f, flagged: false } : f));
      return json({ fact_id: params.id, flagged: false });
    }),
    http.get(`${c}/pages/:n`, ({ params }) =>
      json(pageDetail(Number(params.n), { read: api.read || Boolean(opts.pageSaysRead), in_period: opts.inPeriod ?? true, first_read_at: api.read ? "2026-10-18T10:00:00Z" : null })),
    ),
    http.put(`${c}/pages/:n/transcription`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.puts.push({ page: Number(params.n), body });
      if (api.putStatus) return apiError(api.putStatus, api.putCode ?? "unavailable");
      queue = queue.map((p) => (p.page_no === Number(params.n) ? { ...p, done: true, has_transcription: true } : p));
      return json({ job_id: "77777777-7777-4777-8777-777777777777" }, 202);
    }),
    http.patch(`${c}/pages/:n`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.patches.push({ page: Number(params.n), body });
      queue = queue.map((p) => (p.page_no === Number(params.n) ? { ...p, needs_clearer_copy: Boolean(body.needs_clearer_copy) } : p));
      return json(pageDetail(Number(params.n), { needs_clearer_copy: true }));
    }),
  );
  return api;
}
