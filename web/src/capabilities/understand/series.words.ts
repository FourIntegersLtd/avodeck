// Words for the series bars on screen 11, registered under `understand.`. A bar's own label is copied from the
// record and arrives from the API; nothing here names a medicine, a lane or a kind (rule 11).
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "series.issues_one": "{{count}} issue from {{first}} to {{last}}",
  "series.issues_other": "{{count}} issues from {{first}} to {{last}}",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`understand.${key}`, value])));

export function seriesIssues(count: number, first: string, last: string): string {
  return t("understand.series.issues", { count, first, last });
}
