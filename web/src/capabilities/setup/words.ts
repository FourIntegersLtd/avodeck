// Setup's own words (docs/spec/glossary.md), registered under "setup." beside the shared catalogue.
// `sw()` only accepts keys defined here.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "upload.saved": "Saved: {{files}}.",
  "upload.not_saved": "Not saved: {{files}}.",
  "questions.changed": "A question changed while you were here. We've shown the latest. Please check them and confirm again.",
  "template.stale_show": "{{count}} headings matched to report sections",
  "new_case.template_locked": "{{name}}'s report template is used for this case. You can't change it here.",
  "new_case.oral_hint": "You need to record oral instructions too. Write them in your own words.",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`setup.${key}`, value])));

export function sw(key: keyof typeof WORDS, params?: Record<string, string | number>): string {
  return t(`setup.${key}`, params);
}
