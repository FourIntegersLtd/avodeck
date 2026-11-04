// Small helpers the four report screens share: waiting for a queued job, version tags, dates and money.
import { useEffect, useState } from "react";
import { humanise } from "../../foundation/i18n";
import { DEFAULT_LOCALE, formatDay } from "../../foundation/i18n/dates";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useOnCaseChange } from "../../foundation/lib/sse";
import { useSession } from "../../foundation/lib/session";

export const versionTag = (version: number | string) => `"${version}"`;

const WAIT_LIMIT_MS = 120_000;

/** After queuing a job, reloads when the case's jobs change (over the live stream) until `signal` (for example
 * the newest version id) changes, and stops waiting after two minutes. */
export function useWaiting(reload: () => void, signal: unknown) {
  const { item } = useCaseFrame();
  const [from, setFrom] = useState<{ signal: unknown } | null>(null);
  useOnCaseChange(from ? item.id : null, reload);
  useEffect(() => {
    if (!from) return;
    const timer = window.setTimeout(() => setFrom(null), WAIT_LIMIT_MS); // clock, not data
    return () => window.clearTimeout(timer);
  }, [from]);
  useEffect(() => {
    if (from && signal !== from.signal) setFrom(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal]);
  return { waiting: from !== null, start: () => setFrom({ signal }) };
}

export function useLocale(): string {
  return useSession().me.person.locale || DEFAULT_LOCALE;
}

/** A calendar day (YYYY-MM-DD) or a moment, as "28 November 2026". */
export const longDate = (value: string, locale: string): string => formatDay(value, locale, "long");

export function money(amount: string | number, currency: string, locale: string): string {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function downloadPath(caseId: string, versionId: string, format: "docx" | "pdf"): string {
  return `/v1/cases/${caseId}/report/versions/${versionId}/download?format=${format}`;
}

/** Names a report section or appendix by the heading the case's pack gives it; a key the pack does not list
 * (an expert's own heading) falls back to the key in plain words. */
export function useSectionName(): (key: string) => string {
  const { pack } = useCaseFrame();
  const entries = [...pack.report_sections, ...(pack.report_appendices ?? [])] as Array<{ key: string; label?: string }>;
  return (key) => entries.find((e) => e.key === key)?.label ?? humanise(key);
}
