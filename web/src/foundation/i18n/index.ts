// Every interface string comes from a key (rule 11). en-GB.json holds the foundation's words;
// slices add theirs with registerMessages. Pack words (party names, steps) arrive from the API.
import { DEFAULT_LOCALE, safeLocale } from "./dates";
import base from "./en-GB.json";

type Params = Record<string, string | number | null | undefined>;

const messages: Record<string, string> = { ...(base as Record<string, string>) };
const plurals = new Intl.PluralRules(DEFAULT_LOCALE);

export function registerMessages(more: Record<string, string>): void {
  Object.assign(messages, more);
}

export function hasMessage(key: string): boolean {
  return key in messages || `${key}_other` in messages;
}

function fill(template: string, params: Params): string {
  return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name: string) => String(params[name] ?? ""));
}

/** The words for `key`, with {{name}} filled from `params`; `count` picks the _one/_other form. */
export function t(key: string, params: Params = {}, fallback?: string): string {
  let template = messages[key];
  if (template === undefined && typeof params.count === "number") {
    template = messages[`${key}_${plurals.select(params.count)}`] ?? messages[`${key}_other`];
  }
  if (template === undefined) return fallback ?? key;
  return fill(template, params);
}

/** "read_again" -> "Read again": used when neither the pack nor a slice gives words for a key. */
export function humanise(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A calendar date (YYYY-MM-DD) as day and short month, in the locale's order: "30 Nov" or "Nov 30". */
export function formatDue(isoDate: string, locale: string | null | undefined): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1));
  return new Intl.DateTimeFormat(safeLocale(locale), { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
}

/** A moment as weekday, day and month in the person's timezone: "Wednesday 14 October". */
export function formatWeekdayDate(date: Date, locale: string | null | undefined, timeZone?: string | null): string {
  return new Intl.DateTimeFormat(safeLocale(locale), { weekday: "long", day: "numeric", month: "long", timeZone: timeZone ?? undefined })
    .format(date)
    .replace(",", "");
}

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

const MONTH_MS = 30.4375 * 86_400_000; // the mean calendar month (365.25 / 12 days), not thirty days
const YEAR_MS = 365.25 * 86_400_000;

/** "2 days ago", "in 3 hours", "last month", "now". Months and years are rounded to the nearest calendar unit. */
export function formatRelative(date: Date, locale: string | null | undefined, now: Date = new Date()): string {
  const diff = date.getTime() - now.getTime();
  const format = new Intl.RelativeTimeFormat(safeLocale(locale), { numeric: "auto" });
  const away = Math.abs(diff);
  const sign = diff < 0 ? -1 : 1;
  if (away >= 28 * 86_400_000) {
    const months = Math.round(away / MONTH_MS);
    if (months >= 12) return format.format(sign * Math.max(1, Math.round(away / YEAR_MS)), "year");
    return format.format(sign * months, "month");
  }
  for (const [unit, ms] of UNITS) {
    if (away >= ms) return format.format(Math.round(diff / ms), unit);
  }
  return format.format(0, "second");
}

/** The hour (0-23) now in a timezone, for greetings. */
export function hourIn(timeZone: string | null | undefined, now: Date = new Date()): number {
  const hour = new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: timeZone ?? undefined }).format(now);
  return Number(hour) % 24;
}
