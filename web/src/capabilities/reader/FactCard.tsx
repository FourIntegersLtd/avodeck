// One suggestion: what we found, its state, and the actions the expert can take on it.
import { useId, type ReactNode } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { t } from "../../foundation/i18n";
import { Button, IconTile, StatusBadge } from "../../foundation/ui";
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
  /** While changing, for a fact with a date field: the calendar day and its setter. Absent = no calendar. */
  editEventDate?: string | null;
  onEditEventDate?: (value: string) => void;
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
      {checked && !recheck ? (
        <p className="rd-tick">
          <IconTile tone="mint">✓</IconTile>
          <span>{t("ui.status.checked")}</span>
        </p>
      ) : rejected ? (
        <StatusBadge status="not_right" />
      ) : (
        <p className="rd-state">{t("ui.status.please_check")}</p>
      )}
      <p className="rd-kind">{kindLabel(fact.kind)}</p>
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
      {(fact.related ?? []).map((r) => {
        const live = p.allFacts?.find((f) => f.id === r.fact_id)?.status ?? r.status;
        const done = live === "verified" || live === "edited";
        return (
          <p key={r.fact_id} className="rd-related">
            <IconTile tone={done ? "mint" : "amber"}>{done ? "✓" : "•"}</IconTile>
            <span>
              {w("read.related", { kind: kindLabel(r.kind), date: longDate(r.event_date) })}
              <br />
              {w(done ? "read.related_checked" : "read.related_open", { value: r.value ?? "" })}
            </span>
          </p>
        );
      })}
      {recheck && <p className="rd-note">{w("fact.recheck")}</p>}
      {fact.flagged && (
        <p className="rd-cuts">
          <strong>{w("fact.cuts_against")}</strong>
          <span>{w("fact.in_points_against")}</span>
        </p>
      )}

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
          {p.editEventDate !== null && p.editEventDate !== undefined && (
            <div className="rd-edit__row">
              <label htmlFor={`${id}-ed`} className="rd-label rd-label--small">{w("fact.timeline_date_label")}</label>
              <input id={`${id}-ed`} className="av-input" type="date" value={p.editEventDate} onChange={(e) => p.onEditEventDate?.(e.target.value)} />
            </div>
          )}
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
          <p className="rd-hint">{w("fact.fix_one_line")}</p>
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
