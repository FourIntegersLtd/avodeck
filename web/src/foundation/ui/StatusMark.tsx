import { t } from "../i18n";
import type { Status } from "./StatusBadge";

/** The round mark beside a row: checked (solid tick), please check (dashed), not right (red cross), look at (amber ring). */
export function StatusMark({ status }: { status: Status }) {
  return (
    <span className={`av-mark av-mark--${status}`} role="img" aria-label={t(`ui.status.${status}`)}>
      {status === "checked" && (
        <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12l5 5 9-10" />
        </svg>
      )}
      {status === "not_right" && (
        <svg width="11" height="11" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3.6" strokeLinecap="round">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      )}
    </span>
  );
}
