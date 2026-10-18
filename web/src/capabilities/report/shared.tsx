// Small helpers the four report screens share: waiting for a queued job, version tags, dates and money.
import { useEffect, useState } from "react";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useSession } from "../../foundation/lib/session";

export const versionTag = (version: number | string) => `"${version}"`;

const POLL_MS = 1000;
const POLL_LIMIT = 120;

/** After queuing a job, reloads every second until `signal` (for example the newest version id) changes. */
export function useWaiting(reload: () => void, signal: unknown) {
  const [from, setFrom] = useState<{ signal: unknown } | null>(null);
  useEffect(() => {
    if (!from) return;
    let beats = 0;
    const timer = window.setInterval(() => {
      beats += 1;
      if (beats > POLL_LIMIT) setFrom(null);
      else reload();
    }, POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from]);
  useEffect(() => {
    if (from && signal !== from.signal) setFrom(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal]);
  return { waiting: from !== null, start: () => setFrom({ signal }) };
}

export function useLocale(): string {
  return useSession().me.person.locale || "en-GB";
}

/** A calendar day (YYYY-MM-DD) or a moment, as "28 November 2026". */
export function longDate(value: string, locale: string): string {
  const day = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00Z`) : new Date(value);
  try {
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(day);
  } catch {
    return value;
  }
}

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
