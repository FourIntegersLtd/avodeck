import type { ReactNode } from "react";
import { SafeLink as Link } from "./SafeLink";
import { t } from "../i18n";

export type RowStatus = "ok" | "warn" | "stop";

const ICONS: Record<RowStatus, ReactNode> = {
  ok: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  warn: (
    <>
      <path d="M12 8v5" />
      <path d="M12 16.5v.01" />
    </>
  ),
  stop: <path d="M7 7l10 10M17 7L7 17" />,
};

export interface StatusRowProps {
  status: RowStatus;
  label: ReactNode;
  count?: number;
  to?: string;
  linkLabel?: string;
}

/** One line of a checklist: an icon and a word for its state (never colour alone), then a count and a link. */
export function StatusRow({ status, label, count, to, linkLabel }: StatusRowProps) {
  return (
    <li className={`av-status-row av-status-row--${status}`}>
      <svg className="av-status-row__icon" width="28" height="28" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="11" strokeWidth="1.5" />
        {ICONS[status]}
      </svg>
      <span className="av-visually-hidden">{t(`ui.status_row.${status}`)}</span>
      <span className="av-status-row__label">{label}</span>
      {count !== undefined && <span className="av-mono av-status-row__count">{count}</span>}
      {to && linkLabel && (
        <Link className="av-text-link" to={to}>
          {linkLabel}
        </Link>
      )}
    </li>
  );
}
