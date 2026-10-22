// Small helpers shared by the trial screens: pack words, dates in the case's locale, saving a file the
// API sent, and sending a PDF as the request body.
import type { Schemas } from "../../foundation/platform-client";
import type { Frame } from "./frame";
import { w } from "./words";

/** A word from the case pack's vocabulary (for example `court_bundle`); empty if the pack has none. */
export function vocab(pack: Schemas["CasePack"] | undefined, key: string): string {
  const value = (pack?.vocabulary as Record<string, unknown> | undefined)?.[key];
  return typeof value === "string" ? value : "";
}

/** A calendar date (YYYY-MM-DD) or moment as "19 February 2027", in the given locale. */
export function longDate(iso: string, locale?: string | null): string {
  const date = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso);
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" };
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, options).format(date);
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
}

/** Saves a file the API sent (never a direct storage link, rule 4). */
export function saveBlob(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  try {
    link.click();
  } catch {
    // jsdom has no navigation; the browser saves the file
  }
  link.remove();
  URL.revokeObjectURL(href);
}

async function bytesOf(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

/** Request options that send a chosen PDF file's bytes as the raw body. */
export async function pdfBody(file: File) {
  const body = await bytesOf(file);
  return {
    body: body as never,
    bodySerializer: (b: unknown) => b as BodyInit,
    headers: { "Content-Type": "application/pdf" },
  };
}

/** The ETag for a row version (`"3"`). */
export const tag = (version: number) => `"${version}"`;

/** The pack's word for a source type (a letter, a consultation); the type's own name when the pack has none. */
export function sourceLabel(pack: Schemas["CasePack"], key: string | null | undefined): string {
  if (!key) return "";
  const words = (pack.vocabulary as Record<string, unknown>).source_types as Record<string, string> | undefined;
  return words?.[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** Prints a PDF the API sent: it is loaded into a hidden frame and that frame's print dialog opens. */
export function printBlob(blob: Blob): void {
  const href = URL.createObjectURL(blob);
  const frame = document.createElement("iframe");
  frame.hidden = true;
  frame.title = w("print_frame");
  frame.onload = () => {
    try {
      frame.contentWindow?.print();
    } catch {
      // jsdom has no printing; the browser prints the file
    }
  };
  frame.src = href;
  document.body.appendChild(frame);
  window.setTimeout(() => {
    frame.remove();
    URL.revokeObjectURL(href);
  }, 120_000);
}

/** The instructing party's word from the pack, by the case's instructing party type. */
export function partyWord(frame: Frame): string {
  const type = (frame.item as { instructing_party_type?: string | null }).instructing_party_type;
  const found = frame.pack.instructing_party_types.find((p) => p.key === type);
  return found?.label ?? w("pages.tell_fallback");
}

function format(locale: string | null | undefined, options: Intl.DateTimeFormatOptions, iso: string): string {
  const date = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso);
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, { ...options, timeZone: "UTC" }).format(date);
  } catch {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone: "UTC" }).format(date);
  }
}

/** "19 Feb", as the boards write a deadline. */
export const shortDate = (iso: string, locale?: string | null) => format(locale, { day: "numeric", month: "short" }, iso);

/** "20 May", or "20 May 2026" when the year differs from the reference date's: the board leaves out a year that is plain from the line. */
export function dayMonth(iso: string, locale?: string | null, reference?: string | null): string {
  const sameYear = reference && reference.slice(0, 4) === iso.slice(0, 4);
  return format(locale, sameYear ? { day: "numeric", month: "long" } : { day: "numeric", month: "long", year: "numeric" }, iso);
}

/** "1 Mar 2024", as the boards write a document's date. */
export const mediumDate = (iso: string, locale?: string | null) => format(locale, { day: "numeric", month: "short", year: "numeric" }, iso);

/** A whole number with the locale's thousands separator: "3,914". */
export function num(n: number, locale?: string | null): string {
  try {
    return new Intl.NumberFormat(locale ?? undefined).format(n);
  } catch {
    return new Intl.NumberFormat(undefined).format(n);
  }
}

/** "11 and 4": the locale's way of joining a list. */
export function joined(items: Array<string | number>, locale?: string | null): string {
  const words = items.map(String);
  try {
    return new Intl.ListFormat(locale ?? undefined, { style: "long", type: "conjunction" }).format(words);
  } catch {
    return words.join(", ");
  }
}

/** The bundle's short name on the boards ("TB"): the first letters of the pack's word for it. */
export function bundleAbbr(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word[0]!.toUpperCase())
    .join("");
}

/** "TB 2,407": a court bundle page in the bundle's short name. */
export function trialRef(name: string, page: number, locale?: string | null): string {
  return w("pages.trial_ref", { abbr: bundleAbbr(name), number: num(page, locale) });
}
