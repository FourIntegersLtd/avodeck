import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanise, t } from "../i18n";
import { firstScreenInCase } from "../lib/screens";
import { initials } from "./initials";
import { Logo } from "./Logo";

export interface CaseHeaderProps {
  /** The case's workflow steps, from its pack (`GET /v1/cases/{id}/pack`). */
  steps: Array<{ key: string; screens?: string[] }>;
  current?: string | null;
  title?: ReactNode;
  children?: ReactNode;
  /** Set to draw the case's one bar: logo, case name and details, step links and the avatar. */
  bar?: boolean;
  caseId?: string;
  /** Under the case name: "case type · due 30 Nov". */
  subtitle?: ReactNode;
  /** Whole-number percent done for the steps that can say (by step key), shown on the step's pill. */
  progress?: Record<string, { done: number; total: number; percent: number }>;
  /** The signed-in person's name, for the initials. */
  person?: string | null;
}

/** The step's name: a slice or language file may name it, otherwise the pack key is shown. */
export function stepLabel(key: string): string {
  return t(`steps.${key}`, {}, humanise(key));
}

/** Where a step pill goes: the first of the step's screens that a slice has registered. */
function stepPath(step: { screens?: string[] }, caseId: string | undefined): string | null {
  if (!caseId) return null;
  return firstScreenInCase(step.screens, caseId);
}

/**
 * The case's steps as pills: done steps show a tick, the current step is marked. With `bar` this is the
 * single bar at the top of every case screen; without it, just the title and the pills.
 */
// Big counts read as a percentage, small ones as "x of y".
const PERCENT_FROM = 20;
function stepProgressText({ done, total, percent }: { done: number; total: number; percent: number }) {
  return total >= PERCENT_FROM ? t("ui.case_header.progress", { percent }) : t("ui.case_header.progress_count", { done, total });
}

export function CaseHeader({ steps, current, title, children, bar, caseId, subtitle, person, progress }: CaseHeaderProps) {
  const currentIndex = steps.findIndex((s) => s.key === current);
  const pills = steps.length === 0 ? null : (
    <nav aria-label={t("ui.case_header.steps")} className="av-case-bar__steps">
      <ol className="av-steps">
        {steps.map((step, i) => {
          const state = currentIndex < 0 ? "todo" : i < currentIndex ? "done" : i === currentIndex ? "current" : "todo";
          const to = bar ? stepPath(step, caseId) : null;
          const inner = (
            <span className="av-step__pill">
              {state === "done" ? (
                <svg className="av-step__tick" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12l5 5 9-10" />
                </svg>
              ) : (
                <span className="av-step__marker">{String(i + 1)}</span>
              )}
              {stepLabel(step.key)}
              {progress?.[step.key] && <span className="av-step__pct">{stepProgressText(progress[step.key]!)}</span>}
            </span>
          );
          return (
            <li key={step.key} className={`av-step av-step--${state}`} aria-current={state === "current" ? "step" : undefined}>
              {to ? (
                <Link className="av-step__hit" to={to}>
                  {inner}
                </Link>
              ) : (
                <span className="av-step__hit">{inner}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
  if (!bar) {
    return (
      <div className="av-case-header">
        {title}
        {pills}
        {children}
      </div>
    );
  }
  return (
    <header className="av-case-bar">
      <Logo />
      <span className="av-case-bar__divider" aria-hidden="true" />
      <div className="av-case-bar__name">
        {title}
        {subtitle && <span className="av-case-bar__sub">{subtitle}</span>}
      </div>
      {pills}
      <Link to="/account" className="av-shell__avatar">
        <span className="av-shell__avatar-disc" aria-hidden="true">
          {initials(person)}
        </span>
        <span className="av-visually-hidden">{t("app.nav.account")}</span>
      </Link>
      {children}
    </header>
  );
}
