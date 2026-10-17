import { t } from "../i18n";

export type Status = "checked" | "please_check" | "not_right" | "look";

const MARKS: Record<Status, string> = { checked: "✓", please_check: "?", not_right: "✕", look: "!" };

/** A fact's or item's status in glossary words, with a mark so colour is never the only signal. */
export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`av-badge av-badge--${status}`}>
      <span aria-hidden="true">{MARKS[status]}</span>
      <span>{t(`ui.status.${status}`)}</span>
    </span>
  );
}
