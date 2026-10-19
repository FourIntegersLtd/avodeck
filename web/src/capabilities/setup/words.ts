// Setup's own words (docs/spec/glossary.md), registered under "setup." beside the shared catalogue.
// `sw()` only accepts keys defined here.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "new_case.oral_hint": "You need to record oral instructions too. Write them in your own words.",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`setup.${key}`, value])));

export function sw(key: keyof typeof WORDS): string {
  return t(`setup.${key}`);
}
