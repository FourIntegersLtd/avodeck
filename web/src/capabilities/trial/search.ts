// The witness-box search (S25). Runs on the device with no connection (rule 4's one exception): a page
// number (yours or the court bundle's), a date in the case's date order or written out, or words that must
// all appear, across the served report, the checked facts, the meeting statement and the written answers.
import type { CopyContent } from "./copy";

export interface Hit {
  kind: "fact" | "paragraph" | "item" | "answer";
  text: string;
  /** Paragraph, question or answer number; null for a fact. */
  ref: number | null;
  date: string | null;
  page_no: number | null;
  trial_page_no: number | null;
  factKind: string | null;
  position: string | null;
}

const PAGE = /^(?:p\.?\s*)?(\d{1,6})$/i;

function pageHits(copy: CopyContent, n: number): Hit[] {
  const hits: Hit[] = [];
  for (const f of copy.facts) {
    if (f.page_no === n || f.trial_page_no === n) hits.push(fact(f));
  }
  for (const p of copy.report?.paragraphs ?? []) {
    const page = p.pages.find((x) => x.page_no === n || x.trial_page_no === n);
    if (page) hits.push(paragraph(p, page));
  }
  return hits;
}

function fact(f: CopyContent["facts"][number]): Hit {
  return { kind: "fact", text: f.text, ref: null, date: f.date, page_no: f.page_no, trial_page_no: f.trial_page_no, factKind: f.kind, position: null };
}

function paragraph(p: NonNullable<CopyContent["report"]>["paragraphs"][number], page = p.pages[0]): Hit {
  return { kind: "paragraph", text: p.text, ref: p.para_no, date: null, page_no: page?.page_no ?? null, trial_page_no: page?.trial_page_no ?? null, factKind: null, position: null };
}

function monthNames(locale: string): Map<string, number> {
  const names = new Map<string, number>();
  for (const loc of [locale, "en-GB"]) {
    for (const month of ["long", "short"] as const) {
      let format: Intl.DateTimeFormat;
      try {
        format = new Intl.DateTimeFormat(loc, { month, timeZone: "UTC" });
      } catch {
        continue;
      }
      for (let m = 0; m < 12; m += 1) names.set(format.format(new Date(Date.UTC(2024, m, 1))).toLowerCase().replace(/\.$/, ""), m + 1);
    }
  }
  return names;
}

const pad = (n: number) => String(n).padStart(2, "0");
const valid = (y: number | null, m: number, d: number) => m >= 1 && m <= 12 && d >= 1 && d <= 31 && (y === null || (y >= 1000 && y <= 9999));

/** A typed date as "YYYY-MM-DD", or "-MM-DD" with no year; null if it is not a date. */
export function parseDate(query: string, dateOrder: string, locale: string): string | null {
  const q = query.trim().toLowerCase().replace(/,/g, " ").replace(/\s+/g, " ");
  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(q);
  if (iso) {
    const [y, m, d] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
    return valid(y, m, d) ? `${y}-${pad(m)}-${pad(d)}` : null;
  }
  const numeric = /^(\d{1,4})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$/.exec(q);
  if (numeric) {
    const parts = [numeric[1], numeric[2], numeric[3]].map((x) => (x === undefined ? null : Number(x)));
    const order = dateOrder.toUpperCase().replace(/[^DMY]/g, "") || "DMY";
    const at = (ch: string) => {
      const withYear = parts[2] === null ? order.replace("Y", "") : order;
      const i = withYear.indexOf(ch);
      return i < 0 ? null : parts[i] ?? null;
    };
    const d = at("D");
    const m = at("M");
    let y = parts[2] === null ? null : at("Y");
    if (y !== null && y < 100) y += 2000;
    if (d === null || m === null || !valid(y, m, d)) return null;
    return y === null ? `-${pad(m)}-${pad(d)}` : `${y}-${pad(m)}-${pad(d)}`;
  }
  const months = monthNames(locale);
  const words = q.split(" ");
  let day: number | null = null;
  let month: number | null = null;
  let year: number | null = null;
  for (const word of words) {
    const clean = word.replace(/(st|nd|rd|th)$/, "");
    if (months.has(word.replace(/\.$/, ""))) month = months.get(word.replace(/\.$/, ""))!;
    else if (/^\d{4}$/.test(clean)) year = Number(clean);
    else if (/^\d{1,2}$/.test(clean)) day = Number(clean);
    else return null;
  }
  if (day === null || month === null || !valid(year, month, day)) return null;
  return year === null ? `-${pad(month)}-${pad(day)}` : `${year}-${pad(month)}-${pad(day)}`;
}

function wordHits(copy: CopyContent, query: string): Hit[] {
  const terms = query.toLowerCase().split(/\s+/).map((x) => x.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "")).filter(Boolean);
  if (!terms.length) return [];
  const has = (...texts: (string | null)[]) => {
    const haystack = texts.filter(Boolean).join(" ").toLowerCase();
    return terms.every((term) => haystack.includes(term));
  };
  const hits: Hit[] = copy.facts.filter((f) => has(f.text)).map((f) => fact(f));
  for (const p of copy.report?.paragraphs ?? []) if (has(p.text)) hits.push(paragraph(p));
  for (const item of copy.joint_statement?.items ?? []) {
    if (has(item.text, item.own_position, item.reason)) {
      hits.push({ kind: "item", text: item.text, ref: item.number, date: null, page_no: null, trial_page_no: null, factKind: null, position: item.own_position });
    }
  }
  for (const a of copy.answers) {
    if (has(a.text)) hits.push({ kind: "answer", text: a.text, ref: a.question_no, date: null, page_no: null, trial_page_no: null, factKind: null, position: null });
  }
  return hits;
}

const key = (h: Hit) => `${h.kind}:${h.ref ?? ""}:${h.page_no ?? ""}:${h.text}`;

/** What the witness-box search shows for `query`, most precise first. */
export function search(copy: CopyContent, query: string, locale: string): Hit[] {
  const q = query.trim();
  if (!q) return [];
  const page = PAGE.exec(q);
  if (page) {
    const hits = pageHits(copy, Number(page[1]));
    if (hits.length) return hits;
  }
  const date = parseDate(q, copy.date_order, locale);
  const dated = date
    ? copy.facts
        .filter((f) => f.date && (date.startsWith("-") ? f.date.endsWith(date) : f.date === date))
        .sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""))
        .map((f) => fact(f))
    : [];
  const seen = new Set(dated.map(key));
  return [...dated, ...wordHits(copy, q).filter((h) => !seen.has(key(h)))];
}
