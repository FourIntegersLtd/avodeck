import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanise, t } from "../i18n";
import { firstScreenInCase, screenPath } from "../lib/screens";
import { initials } from "./initials";
import { Logo } from "./Logo";

export interface CaseHeaderProps {
  /** The case's workflow steps, from its pack (`GET /v1/cases/{id}/pack`). */
  steps: Array<{ key: string; screens?: string[] }>;
  current?: string | null;
  /** The route's screen key: marked in the row of the current step's screens. */
  screen?: string;
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
  /** Actions capabilities registered for this case (foundation/lib/slots), already filtered. */
  actions?: Array<{ key: string; labelKey: string; to: string }>;
}

/** The step's name: a slice or language file may name it, otherwise the pack key is shown. */
export function stepLabel(key: string): string {
  return t(`steps.${key}`, {}, humanise(key));
}

/** Where a step pill goes: the first of the step's screens that a slice has registered. */
function stepPath(
  step: { screens?: string[] },
  caseId: string | undefined,
): string | null {
  if (!caseId) return null;
  return firstScreenInCase(step.screens, caseId);
}

/**
 * The case's steps as pills: done steps show a tick, the current step is marked. With `bar` this is the
 * single bar at the top of every case screen; without it, just the title and the pills.
 */
// Big counts read as a percentage, small ones as "x of y".
const PERCENT_FROM = 20;
function stepProgressText({
  done,
  total,
  percent,
}: {
  done: number;
  total: number;
  percent: number;
}) {
  return total >= PERCENT_FROM
    ? t("ui.case_header.progress", { percent })
    : t("ui.case_header.progress_count", { done, total });
}

/** The current step's screens a click can reach: registered inside this case and needing no id. */
export function stepScreens(
  step: { screens?: string[] } | undefined,
  caseId: string | undefined,
): Array<{ key: string; to: string }> {
  if (!step || !caseId) return [];
  const seen = new Set<string>();
  const found: Array<{ key: string; to: string }> = [];
  for (const key of step.screens ?? []) {
    const to = screenPath(key, { caseId });
    if (
      !to ||
      !to.startsWith(`/cases/${caseId}/`) ||
      to.endsWith("/") ||
      seen.has(to)
    )
      continue;
    seen.add(to);
    found.push({ key, to });
  }
  return found;
}

/** A screen's name in the row: a slice or the language file may name it, otherwise the key in plain form. */
export function screenLabel(key: string): string {
  return t(`screens.${key}`, {}, humanise(key));
}

export function CaseHeader({
  steps,
  current,
  screen,
  title,
  children,
  bar,
  caseId,
  subtitle,
  person,
  progress,
  actions,
}: CaseHeaderProps) {
  const currentIndex = steps.findIndex((s) => s.key === current);
  const pills =
    steps.length === 0 ? null : (
      <nav
        aria-label={t("ui.case_header.steps")}
        className="av-case-bar__steps"
      >
        <ol className="av-steps">
          {steps.map((step, i) => {
            const state =
              currentIndex < 0
                ? "todo"
                : i < currentIndex
                  ? "done"
                  : i === currentIndex
                    ? "current"
                    : "todo";
            const to = bar ? stepPath(step, caseId) : null;
            const inner = (
              <span className="av-step__pill">
                {state === "done" ? (
                  <svg
                    className="av-step__tick"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    aria-hidden="true"
                    focusable="false"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M5 12l5 5 9-10" />
                  </svg>
                ) : (
                  <span className="av-step__marker">{String(i + 1)}</span>
                )}
                {stepLabel(step.key)}
                {progress?.[step.key] && (
                  <span className="av-step__pct">
                    {stepProgressText(progress[step.key]!)}
                  </span>
                )}
              </span>
            );
            return (
              <li
                key={step.key}
                className={`av-step av-step--${state}`}
                aria-current={state === "current" ? "step" : undefined}
              >
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
  const here = bar ? stepScreens(steps[currentIndex], caseId) : [];
  const row =
    here.length < 2 ? null : (
      <nav
        aria-label={t("ui.case_header.screens")}
        className="av-case-bar__screens"
      >
        <ul>
          {here.map((x) => (
            <li key={x.key}>
              <Link
                to={x.to}
                aria-current={x.key === screen ? "page" : undefined}
                aria-label={t("ui.case_header.screen_link", { screen: screenLabel(x.key) })}
              >
                {screenLabel(x.key)}
              </Link>
            </li>
          ))}
        </ul>
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
    <>
      <header className="av-case-bar">
        <span className="av-case-bar__home">
          <Logo />
        </span>
        <span className="av-case-bar__divider" aria-hidden="true" />
        <div className="av-case-bar__name">
          {title}
          {subtitle && <span className="av-case-bar__sub">{subtitle}</span>}
        </div>
        {pills}
        {actions && actions.length > 0 && caseId ? (
          <AvatarMenu person={person} settingsTo={`/cases/${caseId}/team`} />
        ) : (
          <Link to="/account" className="av-shell__avatar">
            <span className="av-shell__avatar-disc" aria-hidden="true">
              {initials(person)}
            </span>
            <span className="av-visually-hidden">{t("app.nav.account")}</span>
          </Link>
        )}
        {children}
      </header>
      {row}
    </>
  );
}

/** The avatar as a disclosure button: "Your account", and "Case settings" when the case has any action to show. */
function AvatarMenu({
  person,
  settingsTo,
}: {
  person?: string | null;
  settingsTo: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);
  return (
    <div className="av-avatar-menu" ref={root}>
      <button
        ref={button}
        type="button"
        className="av-shell__avatar"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="av-shell__avatar-disc" aria-hidden="true">
          {initials(person)}
        </span>
        <span className="av-visually-hidden">
          {t("ui.case_header.account")}
        </span>
      </button>
      {open && (
        <ul id={panelId} className="av-avatar-menu__panel" role="list">
          <li>
            <Link to="/account" onClick={() => setOpen(false)}>
              {t("ui.case_header.account")}
            </Link>
          </li>
          <li>
            <Link to={settingsTo} onClick={() => setOpen(false)}>
              {t("ui.case_header.settings")}
            </Link>
          </li>
        </ul>
      )}
    </div>
  );
}
