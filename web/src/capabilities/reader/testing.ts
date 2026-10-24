// Test stand-in for the reader API (MSW). It keeps the server's blind-read rule: facts answer 423 until
// the page's read is recorded. Imported by the reader tests only.
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_ID, fact, JPEG, pageDetail, pageId, pageWords, queuePage, theCase, type PageWord } from "./testData";

export interface ReaderApi {
  factRequests: number;
  wordRequests: number;
  reads: Array<{ page: number; body: { dwell_seconds: number } }>;
  verifies: Array<{ id: string; ifMatch: string | null; body: Record<string, unknown> }>;
  flags: Array<{ id: string; method: string }>;
  puts: Array<{ page: number; body: Record<string, unknown> }>;
  patches: Array<{ page: number; body: Record<string, unknown> }>;
  rotations: Array<{ pageId: string; body: Record<string, unknown> }>;
  /** Every view turn saved (`PUT /pages/{n}/turn`), in order. */
  turns: Array<{ page: number; body: Record<string, unknown> }>;
  /** How many small pictures (the page strip's) were asked for. */
  smallImages: number;
  /** The page list requests, as their query strings. */
  listRequests: string[];
  copies: Array<{ pageId: string; body: Record<string, unknown> }>;
  replaces: Array<{ pageId: string; body: Record<string, unknown> }>;
  appends: Array<Record<string, unknown>>;
  moves: Array<{ id: string; ifMatch: string | null; body: Record<string, unknown> }>;
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
  /** The case's period of interest, as dates (default: none). */
  period?: { start: string; end: string };
  facts?: Schemas["FactOut"][];
  queue?: Schemas["UnreadablePage"][];
  /** The page's words (default: `pageWords()`). */
  words?: PageWord[];
  /** The case's last page number (the page list answers no page after it). */
  lastPage?: number;
  /** The page's reading as one text (default: its words joined). */
  reading?: string;
  /** The page was replaced by a clearer copy: its number, whether the expert read it, and its words. */
  clearer?: { page: number; read: boolean; words: PageWord[] };
  /** What the progress endpoint says (default 3 of 10, 5 minutes left). */
  progress?: { read: number; total: number; seconds_left: number };
  /** How the page is turned for reading when it opens (default 0). */
  turn?: number;
  /** The bundle has pages 1 to `pageCount`, listed a block at a time (cursor and limit) as the server does. */
  pageCount?: number;
  /** Pages the list says were read (with `pageCount`). */
  readPages?: number[];
}

/** Mounts every reader endpoint for a case; returns what it saw. */
export function mockReader(pack: Schemas["CasePack"], opts: ReaderOpts = {}): ReaderApi {
  const pageNo = opts.page ?? 118;
  const api: ReaderApi = {
    factRequests: 0,
    wordRequests: 0,
    reads: [],
    verifies: [],
    flags: [],
    puts: [],
    patches: [],
    rotations: [],
    turns: [],
    smallImages: 0,
    listRequests: [],
    copies: [],
    replaces: [],
    appends: [],
    moves: [],
    facts: opts.facts ?? [fact(1), fact(2)],
    read: opts.alreadyRead ?? false,
  };
  let turn = opts.turn ?? 0;
  const listed = (n: number) => ({ page_no: n, page_id: pageId(n), source_type: null, read: (opts.readPages ?? []).includes(n), in_period: null, unreadable: false, duplicate_of_page_no: null, turn: 0 });
  let queue = opts.queue ?? [queuePage(612), queuePage(640), queuePage(655)];
  const c = `/v1/cases/${CASE_ID}`;
  signedIn({ pack });
  server.use(
    http.get(c, () => json(theCase())),
    http.get(`${c}/pages/unreadable`, () => json({ pages: queue, done: queue.filter((p) => p.done).length, total: queue.length })),
    http.get(`${c}/pages`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      api.listRequests.push(query.toString());
      if (opts.pageCount) {
        const from = Number(query.get("cursor") ?? 0) + 1;
        const limit = Number(query.get("limit") ?? 100);
        const to = Math.min(opts.pageCount, from + limit - 1);
        const pages = from <= to ? Array.from({ length: to - from + 1 }, (_, i) => listed(from + i)) : [];
        return json({ pages, next_cursor: to < opts.pageCount ? to : null });
      }
      const cursor = query.get("cursor");
      if (cursor !== null) {
        const next = Number(cursor) + 1;
        const pages = next <= (opts.lastPage ?? 100_000) ? [{ page_no: next, page_id: "x", source_type: null, read: false, in_period: null, unreadable: false, duplicate_of_page_no: null, turn: 0 }] : [];
        return json({ pages, next_cursor: null });
      }
      return json({ pages: [{ page_no: pageNo, page_id: "x", source_type: null, read: false, in_period: true, unreadable: false, duplicate_of_page_no: null, turn: 0 }], next_cursor: null });
    }),
    http.get(`${c}/progress`, () => json(opts.progress ?? { read: 3, total: 10, seconds_left: 300 })),
    http.get(`${c}/pages/:n/image`, ({ request }) => {
      if (new URL(request.url).searchParams.get("size") === "small") api.smallImages += 1;
      return new HttpResponse(JPEG, { headers: { "Content-Type": "image/jpeg" } });
    }),
    http.put(`${c}/pages/:n/turn`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.turns.push({ page: Number(params.n), body });
      turn = Number(body.degrees);
      return json(pageDetail(Number(params.n), { read: api.read, turn }));
    }),
    http.get(`${c}/pages/:n/facts`, () => {
      api.factRequests += 1;
      return api.read ? json({ facts: api.facts }) : apiError(423, "blind_read");
    }),
    http.get(`${c}/pages/:n/words`, ({ params }) => {
      api.wordRequests += 1;
      const clearer = opts.clearer && Number(params.n) === opts.clearer.page ? opts.clearer : null;
      if (clearer) return clearer.read ? json({ words: clearer.words, reading: clearer.words.map((x) => x.text).join(" ") }) : apiError(423, "blind_read");
      const words = opts.words ?? pageWords();
      return api.read ? json({ words, reading: opts.reading ?? words.map((x) => x.text).join(" ") }) : apiError(423, "blind_read");
    }),
    http.post(`${c}/facts/:id/move`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.moves.push({ id: String(params.id), ifMatch: request.headers.get("If-Match"), body });
      if (api.verifyStatus) return apiError(api.verifyStatus, api.verifyCode ?? "stale");
      const current = api.facts.find((f) => f.id === params.id)!;
      const next = { ...current, version: current.version + 1, word_ids: body.word_ids as string[] };
      api.facts = api.facts.filter((f) => f.id !== next.id);
      return json(next, 200);
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
      const next = {
        ...current,
        status: body.action === "correct" && current.needs_recheck ? current.status : status,
        needs_recheck: false,
        version: current.version + 1,
        value: body.action === "edit" && body.new_value != null ? String(body.new_value) : current.value,
        fields: body.action === "edit" && body.fields != null ? { ...current.fields, ...(body.fields as Record<string, unknown>) } : current.fields,
      };
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
      json(pageDetail(Number(params.n), { read: api.read || Boolean(opts.pageSaysRead), in_period: opts.inPeriod ?? true, period_start: opts.period?.start ?? null, period_end: opts.period?.end ?? null, first_read_at: api.read ? "2026-10-18T10:00:00Z" : null, superseded_by_page_no: opts.clearer && Number(params.n) === pageNo ? opts.clearer.page : null, turn: Number(params.n) === pageNo ? turn : 0 })),
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
      queue = queue.map((p) =>
        p.page_no === Number(params.n)
          ? { ...p, needs_clearer_copy: Boolean(body.needs_clearer_copy) || p.needs_clearer_copy, done: true }
          : p,
      );
      return json(pageDetail(Number(params.n), { needs_clearer_copy: true }));
    }),
    http.post(`${c}/pages/:id/rotate`, async ({ params, request }) => {
      api.rotations.push({ pageId: String(params.id), body: (await request.json()) as Record<string, unknown> });
      return json({ job_id: "77777777-7777-4777-8777-777777777777" }, 202);
    }),
    http.put(`${c}/pages/:id/duplicate`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.copies.push({ pageId: String(params.id), body });
      queue = queue.map((p) => (p.page_id === params.id ? { ...p, is_copy: true, done: true } : p));
      return json({ page_id: params.id, page_no: 0, copy_of_page_id: null, copy_of_page_no: body.copy_of_page_no, status: "confirmed", version: 1 });
    }),
    http.post(`${c}/documents/append`, async ({ request }) => {
      api.appends.push((await request.json()) as Record<string, unknown>);
      return json({ job_id: "77777777-7777-4777-8777-777777777777", bundle_version: 2 }, 202);
    }),
    http.post(`${c}/pages/:id/replace`, async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      api.replaces.push({ pageId: String(params.id), body });
      queue = queue.map((p) => (p.page_id === params.id ? { ...p, has_clearer_copy: true, done: true } : p));
      return json({ page_id: params.id, document_id: body.document_id, new_page_id: null, new_page_no: null });
    }),
  );
  return api;
}
