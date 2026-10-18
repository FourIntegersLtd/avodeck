// One suggestion: what we found, its state, and the actions the expert can take on it.
import { useId, type ReactNode } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { Button, StatusBadge } from "../../foundation/ui";
import { kindLabel } from "./data";
import { w } from "./words";

export interface FactCardProps {
  fact: Schemas["FactOut"];
  pack: Schemas["CasePack"];
  focused: boolean;
  busy: boolean;
  editing: string | null;
  /** While changing: whether Save may go (a "Please check" fact needs words chosen first). */
  canSave?: boolean;
  /** While changing: toggles for typed words that have no place on the picture. */
  typedWords?: ReactNode;
  reason: string;
  onFocus: () => void;
  onReason: (text: string) => void;
  onCorrect: () => void;
  onNotRight: () => void;
  onStartEdit: () => void;
  onEditText: (text: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onFlag: () => void;
}

/** What the server's state machine lets the expert do with this fact (docs/spec/state-machines.md "Fact").
 * A checked fact flagged "Please check" may be confirmed again, or changed to cite words chosen again. */
export function allowed(fact: Schemas["FactOut"]): { correct: boolean; notRight: boolean; edit: boolean } {
  const checked = fact.status === "verified" || fact.status === "edited";
  return {
    correct: fact.status === "suggested" || fact.status === "rejected" || (checked && fact.needs_recheck === true),
    notRight: fact.status === "suggested" || checked,
    edit: fact.status === "suggested" || (checked && fact.needs_recheck === true),
  };
}

export function FactCard(p: FactCardProps) {
  const { fact } = p;
  const id = useId();
  const can = allowed(fact);
  const checked = fact.status === "verified" || fact.status === "edited";
  const rejected = fact.status === "rejected";
  const recheck = checked && fact.needs_recheck === true;
  const cls = ["rd-card", p.focused && "rd-card--focus", checked && "rd-card--checked", rejected && "rd-card--rejected"].filter(Boolean).join(" ");
  return (
    <article className={cls} aria-labelledby={`${id}-t`} onFocusCapture={p.onFocus} onClick={p.onFocus}>
      <StatusBadge status={recheck ? "please_check" : checked ? "checked" : rejected ? "not_right" : "please_check"} />
      <p className="rd-kind">{kindLabel(fact.kind)}</p>
      <h3 id={`${id}-t`} className="rd-value">
        {fact.value}
      </h3>
      {recheck && <p className="rd-note">{w("fact.recheck")}</p>}
      {fact.flagged && <p className="rd-note">{w("fact.in_points_against")}</p>}

      {rejected ? (
        <div className="rd-actions">
          <Button variant="secondary" disabled={p.busy} onClick={p.onCorrect}>
            {w("fact.undo")}
          </Button>
        </div>
      ) : p.editing !== null ? (
        <div className="rd-edit">
          <label htmlFor={`${id}-e`} className="rd-label">
            {w("fact.right_words")}
          </label>
          <input
            id={`${id}-e`}
            className="av-input"
            autoFocus
            value={p.editing}
            onChange={(e) => p.onEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") p.onCancelEdit();
              if (e.key === "Enter") p.onSaveEdit();
            }}
          />
          <p className="rd-note">{w("fact.choose_words")}</p>
          {p.typedWords}
          <div className="rd-actions">
            <Button disabled={p.busy || !p.editing.trim() || p.canSave === false} onClick={p.onSaveEdit}>
              {w("fact.save_change")}
            </Button>
            <Button variant="secondary" onClick={p.onCancelEdit}>
              {w("fact.cancel")}
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="rd-actions">
            {can.correct && (
              <Button disabled={p.busy} onClick={p.onCorrect} aria-keyshortcuts="1">
                {w("fact.correct")}
              </Button>
            )}
            {can.notRight && (
              <Button variant="secondary" disabled={p.busy} onClick={p.onNotRight} aria-keyshortcuts="2">
                {w("fact.not_right")}
              </Button>
            )}
            {can.edit && (
              <Button variant="secondary" disabled={p.busy} onClick={p.onStartEdit} aria-keyshortcuts="3">
                {w("fact.change")}
              </Button>
            )}
          </div>
          <label className="rd-reason">
            <span>{w("fact.why_not")}</span>
            <input className="av-input" value={p.reason} maxLength={200} onChange={(e) => p.onReason(e.target.value)} />
          </label>
          {checked && (
            <Button variant="secondary" disabled={p.busy} onClick={p.onFlag}>
              {fact.flagged ? w("fact.points_against_off") : w("fact.points_against")}
            </Button>
          )}
        </>
      )}
    </article>
  );
}
