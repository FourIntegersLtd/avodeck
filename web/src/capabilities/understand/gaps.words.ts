// Words for the timeline's gaps (en-GB), registered under `understand.`. The server sends a label key; a rule with
// no words here falls back to the plain count in words.ts (`timeline.gap`).
import { hasMessage, registerMessages, t } from "../../foundation/i18n";

const WORDS: Record<string, string> = {
  "timeline.gap.no_action_within_one": "{{count}} day. Nothing in the record shows what followed.",
  "timeline.gap.no_action_within_other": "{{count}} days. Nothing in the record shows what followed.",
  "timeline.gap.not_acknowledged_within_one": "{{count}} day. Nothing shows this was acknowledged.",
  "timeline.gap.not_acknowledged_within_other": "{{count}} days. Nothing shows this was acknowledged.",
  "timeline.gap.records_quiet_one": "{{count}} day. The records go quiet here, so pages may be missing.",
  "timeline.gap.records_quiet_other": "{{count}} days. The records go quiet here, so pages may be missing.",
  "timeline.gap.none_after": "Nothing in the record after this.",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`understand.${key}`, value])));

/** The server's words for a gap, or the plain count when this rule has none. */
export function gapWords(labelKey: string, days: number): string {
  const key = `understand.${labelKey}`;
  return hasMessage(key) ? t(key, { count: days }) : t("understand.timeline.gap", { count: days });
}
