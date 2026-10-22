// A question's chip on S11 (card, ribbon pin, side panel, list). One place, so the highlight lives here.
import type { ReactNode } from "react";
import { QChip } from "../../foundation/ui";
import { useHighlight, useHighlightAttrs } from "./highlight";
import { w } from "./highlight.words";

/** Hover only; it is not focusable (the card or row around it is already the one main action). */
export function QuestionChip({ questionId, n }: { questionId: string; n: number }) {
  const { show } = useHighlight();
  return (
    <span onMouseEnter={() => show(questionId, n)} onMouseLeave={() => show(null)}>
      <QChip n={n} />
    </span>
  );
}

/** The keyboard path: a toggle button that lights everything linked to the question. */
export function QuestionToggle({ questionId, n }: { questionId: string; n: number }) {
  const { questionId: lit, show } = useHighlight();
  const pressed = lit === questionId;
  return (
    <button type="button" className="av-question-toggle" aria-pressed={pressed} aria-label={w("highlight.toggle", { n })} onClick={() => show(pressed ? null : questionId, n)}>
      <QChip n={n} />
    </button>
  );
}

/** A list row that fades or lights with the highlight. */
export function HighlightRow({ questionIds, className, children }: { questionIds: readonly string[]; className?: string; children: ReactNode }) {
  return (
    <li className={className} {...useHighlightAttrs(questionIds)}>
      {children}
    </li>
  );
}
