import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanise, t } from "../i18n";
import { screenPath } from "../lib/screens";
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
  for (const screen of step.screens ?? []) {
    const to = screenPath(screen, { caseId });
    if (to) return to;
  }
  return null;
}

/**
 * The case's steps as pills: done steps show a tick, the current step is marked. With `bar` this is the
 * single bar at the top of every case screen; without it, just the title and the pills.
 */
export function CaseHeader({ steps, current, title, children, bar, caseId, subtitle, person }: CaseHeaderProps) {
  const currentIndex = steps.findIndex((s) => s.key === current);
  const pills = steps.length === 0 ? null : (
    <nav aria-label={t("ui.case_header.steps")} className="av-case-bar__steps">
      <ol className="av-steps">
        {steps.map((step, i) => {
          const state = currentIndex < 0 ? "todo" : i < currentIndex ? "done" : i === currentIndex ? "current" : "todo";
          const to = bar ? stepPath(step, caseId) : null;
          const inner = (
            <span className="av-step__pill">
              <span className="av-step__marker" aria-hidden={state === "done" ? true : undefined}>
                {state === "done" ? "✓" : String(i + 1)}
              </span>
              {stepLabel(step.key)}
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
