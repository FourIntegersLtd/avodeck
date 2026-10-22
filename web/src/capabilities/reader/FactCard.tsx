// One suggestion: what we found, its state, and the actions the expert can take on it.
import { useId, type ReactNode } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { t } from "../../foundation/i18n";
import { Button, StatusBadge } from "../../foundation/ui";
import { humanise, kindLabel, longDate } from "./data";
import { w } from "./words";

export interface FactCardProps {
  fact: Schemas["FactOut"];
  pack: Schemas["CasePack"];
  /** Every suggestion on the page, to say whether a related record has been checked. */
  allFacts?: Schemas["FactOut"][];
  focused: boolean;
  busy: boolean;
  editing: string | null;
  editingFields: Record<string, string> | null;
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
  onEditField: (key: string, value: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onFlag: () => void;
  timelineDraft?: string | null;
  onStartTimelineDate?: () => void;
  onTimelineDateChange?: (value: string) => void;
  onSaveTimelineDate?: () => void;
  onCancelTimelineDate?: () => void;
}

/** What the server's state machine lets the expert do with this fact (docs/spec/state-machines.md "Fact").
 * A checked fact flagged "Please check" may be confirmed again, or changed to cite words chosen again. */
export function allowed(fact: Schemas["FactOut"]): { correct: boolean; notRight: boolean; edit: boolean; timelineDate: boolean } {
  const checked = fact.status === "verified" || fact.status === "edited";
  return {
    correct: fact.status === "suggested" || fact.status === "rejected" || (checked && fact.needs_recheck === true),
    notRight: fact.status === "suggested" || checked,
    edit: fact.status === "suggested" || (checked && fact.needs_recheck === true),
    timelineDate: checked && !fact.event_date,
  };
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

/** Editable detail rows: pack key, label, display value. Dates in fields are omitted (title carries the date). */
export function editableFields(fact: Schemas["FactOut"]): Array<{ key: string; label: string; value: string }> {
  return Object.entries(fact.fields ?? {}).flatMap(([key, value]) =>
    (typeof value === "string" || typeof value === "number") && String(value).trim() && !ISO_DAY.test(String(value))
      ? [{ key, label: humanise(key), value: String(value) }]
      : [],
  );
}

const TICK = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M5 12l5 5 9-10" />
  </svg>
);
const FLAG = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
  </svg>
);

/** "Prescription · 15 May 2024": the kind and, when the record has one, its day. */
const titleOf = (kind: string, day: string | null | undefined) => (day ? w("read.related", { kind: kindLabel(kind), date: longDate(day) }) : kindLabel(kind));

export function FactCard(p: FactCardProps) {
  const { fact } = p;
  const id = useId();
  const can = allowed(fact);
  const checked = fact.status === "verified" || fact.status === "edited";
  const rejected = fact.status === "rejected";
  const recheck = checked && fact.needs_recheck === true;
  // A checked fact is drawn as one quiet row, as the canvas draws the record found with a visit.
  const compact = checked && !recheck && p.editing === null && !can.timelineDate && (p.timelineDraft === null || p.timelineDraft === undefined);
  const cls = ["rd-card", p.focused && "rd-card--focus", rejected && "rd-card--rejected"].filter(Boolean).join(" ");
  return (
    <article className="rd-fact" aria-labelledby={`${id}-t`} onFocusCapture={p.onFocus} onClick={p.onFocus}>
      {compact ? (
        <div className={`rd-row${p.focused ? " rd-row--focus" : ""}`}>
          <span className="rd-row__tick">{TICK}</span>
          <span className="rd-row__text">
            <strong>{titleOf(fact.kind, fact.event_date)}</strong>
            <br />
            <span className="rd-row__sub">
              <span id={`${id}-t`}>{fact.value}</span>
              {` · ${w("read.checked_by_you")}`}
            </span>
          </span>
          {can.notRight && (
            <Button variant="quiet" className="rd-row__undo" disabled={p.busy} onClick={p.onNotRight} aria-keyshortcuts="2">
              {w("fact.not_right")}
            </Button>
          )}
        </div>
      ) : (
        <div className={cls}>
          {rejected ? <StatusBadge status="not_right" /> : <p className="rd-state">{t("ui.status.please_check")}</p>}
          <p className="rd-kind rd-sr">{kindLabel(fact.kind)}</p>
          <h3 id={`${id}-t`} className="rd-value">
            {fact.value}
          </h3>
          {editableFields(fact).length > 0 && p.editing === null && (
            <dl className="rd-rows">
              {editableFields(fact).map(({ label, value }) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {recheck && <p className="rd-note">{w("fact.recheck")}</p>}

          {rejected ? (
            <div className="rd-actions">
              <Button variant="secondary" disabled={p.busy} onClick={p.onCorrect}>
                {w("fact.undo")}
              </Button>
            </div>
          ) : p.timelineDraft !== null && p.timelineDraft !== undefined ? (
            <div className="rd-edit">
              <label htmlFor={`${id}-td`} className="rd-label">
                {w("fact.timeline_date_label")}
              </label>
              <input id={`${id}-td`} className="av-input" type="date" autoFocus value={p.timelineDraft} onChange={(e) => p.onTimelineDateChange?.(e.target.value)} />
              <div className="rd-actions">
                <Button disabled={p.busy || !p.timelineDraft} onClick={p.onSaveTimelineDate}>
                  {w("fact.save_change")}
                </Button>
                <Button variant="secondary" onClick={p.onCancelTimelineDate}>
                  {w("fact.cancel")}
                </Button>
              </div>
            </div>
          ) : p.editing !== null && p.editingFields !== null ? (
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
                }}
              />
              {editableFields(fact).map(({ key, label }) => (
                <div key={key} className="rd-edit__row">
                  <label htmlFor={`${id}-f-${key}`} className="rd-label rd-label--small">{label}</label>
                  <input
                    id={`${id}-f-${key}`}
                    className="av-input"
                    value={p.editingFields?.[key] ?? ""}
                    onChange={(e) => p.onEditField(key, e.target.value)}
                  />
                </div>
              ))}
              <p className="rd-note">{w("fact.choose_words")}</p>
              {p.typedWords}
              <div className="rd-actions">
                <Button disabled={p.busy || p.canSave === false} onClick={p.onSaveEdit}>
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
              </div>
              <div className="rd-links">
                {can.edit && (
                  <Button variant="quiet" disabled={p.busy} onClick={p.onStartEdit} aria-keyshortcuts="3">
                    {w("fact.change")}
                  </Button>
                )}
                {can.timelineDate && p.onStartTimelineDate && (
                  <Button variant="quiet" disabled={p.busy} onClick={p.onStartTimelineDate}>
                    {w("fact.set_timeline_date")}
                  </Button>
                )}
                {can.notRight && (
                  <details className="rd-why-not">
                    <summary>{w("fact.say_why")}</summary>
                    <label className="rd-reason">
                      <span>{w("fact.why_not")}</span>
                      <input className="av-input" value={p.reason} maxLength={200} onChange={(e) => p.onReason(e.target.value)} />
                    </label>
                  </details>
                )}
              </div>
            </>
          )}
        </div>
      )}
      {(fact.related ?? []).map((r) => {
        const live = p.allFacts?.find((f) => f.id === r.fact_id)?.status ?? r.status;
        const done = live === "verified" || live === "edited";
        return (
          <p key={r.fact_id} className="rd-row rd-related">
            <span className={`rd-row__tick${done ? "" : " rd-row__tick--open"}`}>{done ? TICK : "•"}</span>
            <span className="rd-row__text">
              <strong>{titleOf(r.kind, r.event_date)}</strong>
              <br />
              <span className="rd-row__sub">{w(done ? "read.related_checked" : "read.related_open", { value: r.value ?? "" })}</span>
            </span>
          </p>
        );
      })}
      {checked && p.editing === null && (
        <button type="button" className="rd-flag" disabled={p.busy} onClick={p.onFlag}>
          {FLAG}
          {fact.flagged ? (
            <span>
              {w("fact.cuts_against")}
              <br />
              <span className="rd-flag__sub">{w("fact.saved_to_points")}</span>
              <span className="rd-sr"> {w("fact.points_against_off")}</span>
            </span>
          ) : (
            <span>{w("fact.points_against")}</span>
          )}
        </button>
      )}
    </article>
  );
}
