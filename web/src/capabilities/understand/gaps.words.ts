// Words for the timeline's gaps (en-GB), registered under `understand.`. The server sends a label key; a rule with
// no words here falls back to the plain count in words.ts (`timeline.gap`).
import { hasMessage, registerMessages, t } from "../../foundation/i18n";
import { w } from "./words";

const WORDS: Record<string, string> = {
  "timeline.gap.no_action_within_one": "{{count}} day. Nothing in the record shows what followed.",
  "timeline.gap.no_action_within_other": "{{count}} days. Nothing in the record shows what followed.",
  "timeline.gap.not_acknowledged_within_one": "{{count}} day. Nothing shows this was acknowledged.",
  "timeline.gap.not_acknowledged_within_other": "{{count}} days. Nothing shows this was acknowledged.",
  "timeline.gap.records_quiet_one": "{{count}} day. The records go quiet here, so pages may be missing.",
  "timeline.gap.records_quiet_other": "{{count}} days. The records go quiet here, so pages may be missing.",
  "timeline.gap.none_after": "Nothing in the record after this.",
  "timeline.gap.short_one": "{{count}} day",
  "timeline.gap.short_other": "{{count}} days",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`understand.${key}`, value])));

/** A word from this file (registered above under `understand.`, not in the base language file). */
const word = (key: string, params: Record<string, string | number>) => t(`understand.${key}`, params);

/** The server's words for a gap, or the plain count when this rule has none. */
export function gapWords(labelKey: string, days: number): string {
  const key = `understand.${labelKey}`;
  return hasMessage(key) ? t(key, { count: days }) : w("timeline.gap", { count: days });
}

/** The count alone, for a line too short to carry the sentence. */
export function gapShort(days: number): string {
  return word("timeline.gap.short", { count: days });
}
