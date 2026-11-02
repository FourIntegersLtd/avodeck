// Prefill "Date on timeline" from the pack date field (e.g. 31.12.93 → 1993-12-31), same century rule as the API.
import type { Schemas } from "../../foundation/platform-client";

/** Key of the kind's date field per the pack, or null when the kind has none. */
export function dateFieldKey(pack: Schemas["CasePack"], kind: string): string | null {
  const entry = pack.fact_kinds.find((k) => k.key === kind);
  if (!entry?.fields || typeof entry.fields !== "object") return null;
  for (const [name, type] of Object.entries(entry.fields as Record<string, string>)) {
    if (type === "date") return name;
  }
  return null;
}

function toIso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** The latest of 19yy and 20yy (a real calendar day) that is not after `anchor` (YYYY-MM-DD). Compared as text: ISO days sort. */
function pickPastCentury(day: number, month: number, yy: number, anchor: string): string | null {
  const options: string[] = [];
  for (const base of [1900, 2000]) {
    const year = base + yy;
    const got = new Date(Date.UTC(year, month - 1, day));
    if (got.getUTCFullYear() !== year || got.getUTCMonth() !== month - 1 || got.getUTCDate() !== day) continue;
    const iso = toIso(year, month, day);
    if (iso <= anchor) options.push(iso);
  }
  return options.length ? options.sort().pop()! : null;
}

/** Value for `<input type="date">` when setting the timeline date from the record. */
export function suggestTimelineDate(fact: Schemas["FactOut"], pack: Schemas["CasePack"]): string {
  if (fact.event_date) return fact.event_date.slice(0, 10);
  const key = dateFieldKey(pack, fact.kind);
  const raw = key ? String(fact.fields?.[key] ?? "").trim() : "";
  if (!raw) return "";
  const iso = /^(\d{4}-\d{2}-\d{2})/.exec(raw);
  if (iso) return iso[1]!;
  const m = /^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\s*$/.exec(raw);
  if (!m) return "";
  const order = pack.date_order === "MDY" ? "MDY" : "DMY";
  const day = order === "MDY" ? Number(m[2]) : Number(m[1]);
  const month = order === "MDY" ? Number(m[1]) : Number(m[2]);
  const yearPart = m[3]!;
  if (yearPart.length === 4) return toIso(Number(yearPart), month, day);
  const now = new Date();
  const anchor = toIso(now.getFullYear(), now.getMonth() + 1, now.getDate());
  return pickPastCentury(day, month, Number(yearPart), anchor) ?? "";
}
