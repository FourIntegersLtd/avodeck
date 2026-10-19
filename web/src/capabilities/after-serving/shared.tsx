// Small helpers the five after-serving screens share: waiting for a queued job, dates, labels for pack keys, and a
// labelled select.
import { useEffect, useId, useState, type SelectHTMLAttributes } from "react";
import { hasMessage, humanise, t } from "../../foundation/i18n";
import { useSession } from "../../foundation/lib/session";

export const versionTag = (version: number | string) => `"${version}"`;

const POLL_MS = 1000;
const POLL_LIMIT = 120;

/** After queuing a job, reloads every second until `signal` changes. */
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

/** A calendar day (YYYY-MM-DD) or a moment, as "28 November 2026". Days are the court's own; they never shift. */
export function longDate(value: string, locale: string): string {
  const day = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00Z`) : new Date(value);
  try {
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(day);
  } catch {
    return value;
  }
}

/** The words for a pack key under `after_serving.<group>.`, else the key in plain words. */
export function keyName(group: string, key: string): string {
  const full = `after_serving.${group}.${key}`;
  return hasMessage(full) ? t(full) : humanise(key);
}

export const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The pack's paragraphs of declaration wording, one entry per paragraph. */
export function declarationParts(declarations: Array<{ key?: string; text?: string | null }>): string[] {
  return declarations.flatMap((d) => (d.text ?? "").split("\n\n").filter(Boolean));
}

export function Select({ label, children, ...rest }: { label: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>{label}</label>
      <select id={id} className="av-input" {...rest}>{children}</select>
    </div>
  );
}
