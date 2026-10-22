// What slice B's screens read from the API and the pack, beyond the generated types.
import { type Schemas } from "../../foundation/platform-client";
import { w } from "./words";

export const casePath = (caseId: string) => ({ params: { path: { case_id: caseId } } });

/** Sentence-style words from a code ("phone_consultation" -> "Phone consultation"). */
export function humanise(code: string): string {
  const spaced = code.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** The pack's word for a source type, else the type's own name in plain form. */
export function sourceLabel(pack: Schemas["CasePack"], key: string | null | undefined): string {
  if (!key) return "";
  const words = (pack.vocabulary as Record<string, unknown>).source_types as Record<string, string> | undefined;
  return words?.[key] ?? humanise(key);
}

/** A fact kind in plain form (the pack names the kind; it gives no separate label). */
export const kindLabel = (kind: string) => humanise(kind);

/** A box edge as a CSS percentage of the page (bbox values are fractions of the page). */
export const pct = (fraction: number) => `${+(fraction * 100).toFixed(3)}%`;

export type Box = { x0: number; y0: number; x1: number; y1: number };

/** The fact's box, if the server gave one in the expected shape. */
export function boxOf(fact: Schemas["FactOut"]): Box | null {
  const b = fact.bbox as Partial<Box> | null;
  if (!b || [b.x0, b.y0, b.x1, b.y1].some((v) => typeof v !== "number")) return null;
  return b as Box;
}

/** The person's locale if the browser knows it, else en-GB. */
function localeTag(locale: string | null | undefined): string {
  try {
    new Intl.DateTimeFormat(locale ?? undefined);
    return locale || "en-GB";
  } catch {
    return "en-GB";
  }
}

/** A whole number with the thousands separator of the person's locale ("1,231"). */
export const count = (n: number, locale: string | null | undefined) => new Intl.NumberFormat(localeTag(locale)).format(n);

/** "Feb 2024 to Aug 2024" in the pack's language and date order; empty when the case has no period. */
export function periodRange(start: string | null | undefined, end: string | null | undefined, locale: string | null | undefined): string {
  if (!start || !end) return "";
  const tag = localeTag(locale);
  const fmt = (iso: string, withYear: boolean) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Intl.DateTimeFormat(tag, withYear ? { month: "short", year: "numeric", timeZone: "UTC" } : { month: "short", timeZone: "UTC" }).format(new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1)));
  };
  // Inside one year the year is said once, at the end ("Feb to Aug 2024"), as the canvas writes it.
  const sameYear = start.slice(0, 4) === end.slice(0, 4);
  return w("read.period_range", { from: fmt(start, !sameYear), to: fmt(end, true) });
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2024-05-15" as "15 May 2024"; fixed, so it does not depend on the pack's locale. */
export function longDate(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "";
}
