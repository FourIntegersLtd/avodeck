import type { ReactNode } from "react";
import { humanise, t } from "../i18n";

export interface CaseHeaderProps {
  /** The case's workflow steps, from its pack (`GET /v1/cases/{id}/pack`). */
  steps: Array<{ key: string }>;
  current?: string | null;
  title?: ReactNode;
  children?: ReactNode;
}

/** The step's name: a slice or language file may name it, otherwise the pack key is shown. */
export function stepLabel(key: string): string {
  return t(`steps.${key}`, {}, humanise(key));
}

/** The case title and its step pills: done steps show a tick, the current step is marked. */
export function CaseHeader({ steps, current, title, children }: CaseHeaderProps) {
  const currentIndex = steps.findIndex((s) => s.key === current);
  return (
    <div className="av-case-header">
      {title}
      <nav aria-label={t("ui.case_header.steps")}>
        <ol className="av-steps">
          {steps.map((step, i) => {
            const state = currentIndex < 0 ? "todo" : i < currentIndex ? "done" : i === currentIndex ? "current" : "todo";
            return (
              <li key={step.key} className={`av-step av-step--${state}`} aria-current={state === "current" ? "step" : undefined}>
                <span className="av-step__marker" aria-hidden={state === "done" ? true : undefined}>
                  {state === "done" ? "✓" : String(i + 1)}
                </span>
                {stepLabel(step.key)}
              </li>
            );
          })}
        </ol>
      </nav>
      {children}
    </div>
  );
}
