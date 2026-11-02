// Calendar days, numbers and lists in the person's or the pack's locale (display only). A calendar day
// (YYYY-MM-DD) is the court's own: it is read and written in UTC, so it never shifts with the device's time zone
// or a daylight-saving change. The locale is always passed in (from the pack or the person); the only constant
// is the message locale that the base words are written in.

export const DEFAULT_LOCALE = "en-GB";

export type DayStyle = "long" | "medium" | "short" | "dayMonth" | "weekday" | "monthYear" | "monthYearShort";

const STYLES: Record<DayStyle, Intl.DateTimeFormatOptions> = {
  long: { day: "numeric", month: "long", year: "numeric" },
  medium: { day: "numeric", month: "short", year: "numeric" },
  short: { day: "numeric", month: "short" },
  dayMonth: { day: "numeric", month: "long" },
  weekday: { weekday: "long", day: "numeric", month: "long" },
  monthYear: { month: "long", year: "numeric" },
  monthYearShort: { month: "short", year: "numeric" },
};

const cache = new Map<string, Intl.DateTimeFormat | Intl.NumberFormat | Intl.ListFormat>();

function cached<T extends Intl.DateTimeFormat | Intl.NumberFormat | Intl.ListFormat>(key: string, make: () => T): T {
  let found = cache.get(key) as T | undefined;
  if (!found) {
    found = make();
    cache.set(key, found);
  }
  return found;
}

/** The locale if the browser knows it, else the message locale. */
export function safeLocale(locale: string | null | undefined): string {
  if (!locale) return DEFAULT_LOCALE;
  try {
    return Intl.getCanonicalLocales(locale)[0] ?? DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** A calendar day as midday UTC (never near a day boundary); a full moment as itself. */
export function parseDay(value: string): Date {
  return DAY.test(value) ? new Date(`${value}T12:00:00Z`) : new Date(value);
}

/** "19 February 2027", "19 Feb", "Friday 19 February"... in the given locale. */
export function formatDay(value: string, locale: string | null | undefined, style: DayStyle = "long"): string {
  const tag = safeLocale(locale);
  const date = parseDay(value);
  if (Number.isNaN(date.getTime())) return value;
  const format = cached(`d:${tag}:${style}`, () => new Intl.DateTimeFormat(tag, { ...STYLES[style], timeZone: "UTC" }));
  const text = format.format(date);
  return style === "weekday" ? text.replace(",", "") : text;
}

function utcMidnight(value: string): number {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
}

function isoOf(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Whole days from `a` to `b` (negative when `b` is earlier). Daylight saving never changes the answer. */
export function daysBetween(a: string, b: string): number {
  return Math.round((utcMidnight(b) - utcMidnight(a)) / 86_400_000);
}

export function addDays(value: string, days: number): string {
  return isoOf(utcMidnight(value) + days * 86_400_000);
}

/** Months added, clamped to the last day of the month: 31 Jan + 1 month = 28 Feb (29 in a leap year). */
export function addMonths(value: string, months: number): string {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  const total = y! * 12 + (m! - 1) + months;
  const year = Math.floor(total / 12);
  const month = total - year * 12;
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return isoOf(Date.UTC(year, month, Math.min(d!, last)));
}

export function addYears(value: string, years: number): string {
  return addMonths(value, years * 12);
}

/** A whole number with the locale's separators. */
export function formatNumber(n: number, locale: string | null | undefined): string {
  const tag = safeLocale(locale);
  return cached(`n:${tag}`, () => new Intl.NumberFormat(tag)).format(n);
}

/** "11 and 4": the locale's way of joining a list. */
export function formatList(items: Array<string | number>, locale: string | null | undefined): string {
  const words = items.map(String);
  const tag = safeLocale(locale);
  try {
    return cached(`l:${tag}`, () => new Intl.ListFormat(tag, { style: "long", type: "conjunction" })).format(words);
  } catch {
    return words.join(", ");
  }
}
