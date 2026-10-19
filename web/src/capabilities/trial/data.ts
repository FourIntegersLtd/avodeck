// Small helpers shared by the trial screens: pack words, dates in the case's locale, saving a file the
// API sent, and sending a PDF as the request body.
import type { Schemas } from "../../foundation/platform-client";

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
