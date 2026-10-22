// Words for the question highlight (S11), registered under `understand.`.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "highlight.announce": "Showing entries linked to Q{{n}}",
  "highlight.toggle": "Light up everything linked to Q{{n}}",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`understand.${key}`, value])));

export function w(key: keyof typeof WORDS, params?: Record<string, string | number>): string {
  return t(`understand.${key}`, params);
}
