// One suggestion: what we found, its state, and the actions the expert can take on it.
import { useId } from "react";
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

export function FactCard(p: FactCardProps) {
  const { fact } = p;
  const id = useId();
  const checked = fact.status === "verified" || fact.status === "edited";
  const rejected = fact.status === "rejected";
  const cls = ["rd-card", p.focused && "rd-card--focus", checked && "rd-card--checked", rejected && "rd-card--rejected"].filter(Boolean).join(" ");
  return (
    <article className={cls} aria-labelledby={`${id}-t`} onFocusCapture={p.onFocus} onClick={p.onFocus}>
      <StatusBadge status={checked ? "checked" : rejected ? "not_right" : "please_check"} />
      <p className="rd-kind">{kindLabel(fact.kind)}</p>
      <h3 id={`${id}-t`} className="rd-value">
        {fact.value}
      </h3>
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
          <div className="rd-actions">
            <Button disabled={p.busy || !p.editing.trim()} onClick={p.onSaveEdit}>
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
            {!checked && (
              <Button disabled={p.busy} onClick={p.onCorrect} aria-keyshortcuts="1">
                {w("fact.correct")}
              </Button>
            )}
            <Button variant="secondary" disabled={p.busy} onClick={p.onNotRight} aria-keyshortcuts="2">
              {w("fact.not_right")}
            </Button>
            <Button variant="secondary" disabled={p.busy} onClick={p.onStartEdit} aria-keyshortcuts="3">
              {w("fact.change")}
            </Button>
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
