// One suggestion: what we found, its state, and the actions the expert can take on it.
import { useId, useState, type ReactNode } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { t } from "../../foundation/i18n";
import { Button, Checkbox, IconTile, LinkButton } from "../../foundation/ui";
import { fieldWord } from "../../foundation/lib/packWords";
import { kindLabel, longDate } from "./data";
import { DotsIcon } from "./icons";
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
  /** An error from this form's Save, shown right under the buttons. */
  formError?: string | null;
  /** A next step for this form's error (e.g. the supplementary report for a served fact), as a link. */
  formAction?: { label: string; to: string } | null;
  /** The clearer copy that replaced this page, if any: the fact may be moved there. */
  clearerPage?: number | null;
  /** While moving to the clearer copy: its page, and what stops the move (with a link when it must be read first). */
  moving?: { page: number; pageHref: string; problem: string | null; readFirst: boolean } | null;
  onStartMove?: () => void;
  onSaveMove?: () => void;
  onCancelMove?: () => void;
  /** When the words weren't on the page: where to type the page out instead. */
  typeOutHref?: string;
  /** When the words weren't on the page: first suggest ticking "These words were read wrongly". */
  tickHint?: boolean;
  /** While changing with words chosen: the "read wrongly" tick ("The right words" is then the expert's reading). */
  misread?: { ticked: boolean } | null;
  onMisread?: (ticked: boolean) => void;
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
 * A checked fact may still be changed (the server refuses one in a served report); one flagged "Please
 * check" may also be confirmed again. */
export function allowed(fact: Schemas["FactOut"]): { correct: boolean; notRight: boolean; edit: boolean; timelineDate: boolean } {
  const checked = fact.status === "verified" || fact.status === "edited";
  return {
    correct: fact.status === "suggested" || fact.status === "rejected" || (checked && fact.needs_recheck === true),
    notRight: fact.status === "suggested" || checked,
    edit: fact.status === "suggested" || checked,
    timelineDate: checked && !fact.event_date,
  };
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

/** The pack's field types for a kind ({field: "string" | "integer" | "number" | "date" | "boolean"}). */
export function fieldTypes(pack: Schemas["CasePack"], kind: string): Record<string, string> {
  const fields = pack.fact_kinds.find((k) => k.key === kind)?.fields;
  return fields && typeof fields === "object" ? (fields as Record<string, string>) : {};
}

/** Editable detail rows: pack key, the pack's label, display value. Dates are omitted: the title carries the
 * date, and the date detail is set by "Date on timeline". */
export function editableFields(fact: Schemas["FactOut"], pack: Schemas["CasePack"]): Array<{ key: string; label: string; value: string }> {
  const types = fieldTypes(pack, fact.kind);
  return Object.entries(fact.fields ?? {}).flatMap(([key, value]) =>
    (typeof value === "string" || typeof value === "number") && String(value).trim() && !ISO_DAY.test(String(value)) && types[key] !== "date"
      ? [{ key, label: fieldWord(pack, fact.kind, key), value: String(value) }]
      : [],
  );
}

export function FactCard(p: FactCardProps) {
  const { fact } = p;
  const fields = editableFields(fact, p.pack);
  const id = useId();
  const [moreOpen, setMoreOpen] = useState(false);
  const can = allowed(fact);
  const checked = fact.status === "verified" || fact.status === "edited";
  const rejected = fact.status === "rejected";
  const recheck = checked && fact.needs_recheck === true;
  const cls = ["rd-card", p.focused && "rd-card--focus", checked && "rd-card--checked", rejected && "rd-card--rejected"].filter(Boolean).join(" ");
  const formProblem = p.formError ? (
    <div className="rd-form-error">
      <p role="alert" className="rd-form-error__text">{p.formError}</p>
      {p.tickHint && <p className="rd-hint">{w("fact.misread_hint")}</p>}
      {p.formAction && (
        <LinkButton variant="secondary" to={p.formAction.to}>
          {p.formAction.label}
        </LinkButton>
      )}
      {p.typeOutHref && (
        <LinkButton variant="secondary" to={p.typeOutHref}>
          {w("fact.type_out_page")}
        </LinkButton>
      )}
    </div>
  ) : null;
  return (
    <article className={cls} aria-labelledby={`${id}-t`} onFocusCapture={p.onFocus} onClick={p.onFocus}>
      {/* The row above already shows the mark, the kind and the words; a screen reader still gets them here. */}
      <p className="rd-sr">{t(checked && !recheck ? "ui.status.checked" : rejected ? "ui.status.not_right" : "ui.status.please_check")}</p>
      <p className="rd-sr rd-kind">{kindLabel(p.pack, fact.kind)}</p>
      <h3 id={`${id}-t`} className="rd-sr rd-value">
        {fact.value}
      </h3>
      {fields.length > 0 && p.editing === null && (
        <dl className="rd-rows">
          {fields.map(({ label, value }) => (
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
              {w("read.related", { kind: kindLabel(p.pack, r.kind), date: longDate(r.event_date) })}
              <br />
              {w(done ? "read.related_checked" : "read.related_open", { value: r.value ?? "" })}
            </span>
          </p>
        );
      })}
      {fact.cites_correction && <p className="rd-read-by-you">{w("fact.read_by_you")}</p>}
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
      ) : p.moving ? (
        <div className="rd-edit">
          <p className="rd-note">{w("fact.move_choose", { page: p.moving.page })}</p>
          <div className="rd-actions">
            <Button disabled={p.busy || p.moving.readFirst} onClick={p.onSaveMove}>
              {w("fact.move_here")}
            </Button>
            <Button variant="secondary" onClick={p.onCancelMove}>
              {w("fact.cancel")}
            </Button>
          </div>
          {p.moving.problem && (
            <div className="rd-form-error">
              <p role="alert" className="rd-form-error__text">{p.moving.problem}</p>
              {p.moving.readFirst && (
                <LinkButton variant="secondary" to={p.moving.pageHref}>
                  {w("fact.move_open_page", { page: p.moving.page })}
                </LinkButton>
              )}
            </div>
          )}
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
          {formProblem}
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
              if (e.key === "Enter" && !p.busy) p.onSaveEdit();
            }}
          />
          {fields.map(({ key, label }) => (
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
          {!p.misread && <p className="rd-note">{w("fact.choose_words")}</p>}
          {p.typedWords}
          {p.misread && (
            <div className="rd-misread">
              <Checkbox label={w("fact.misread_tick")} checked={p.misread.ticked} onChange={(e) => p.onMisread?.(e.target.checked)} />
              {p.misread.ticked && <p className="rd-hint">{w("fact.misread_uses_right_words")}</p>}
            </div>
          )}
          <div className="rd-actions">
            <Button disabled={p.busy || p.canSave === false} onClick={p.onSaveEdit}>
              {w("fact.save_change")}
            </Button>
            <Button variant="secondary" onClick={p.onCancelEdit}>
              {w("fact.cancel")}
            </Button>
          </div>
          {formProblem}
        </div>
      ) : (
        <>
          {can.correct && (
            <Button className="rd-correct" disabled={p.busy} onClick={p.onCorrect} aria-keyshortcuts="1">
              {w("fact.correct")}
            </Button>
          )}
          <div className="rd-actions rd-actions--quiet">
            {can.notRight && (
              <Button variant="quiet" disabled={p.busy} onClick={p.onNotRight} aria-keyshortcuts="2">
                {w("fact.not_right")}
              </Button>
            )}
            {can.edit && (
              <Button variant="quiet" disabled={p.busy} onClick={p.onStartEdit} aria-keyshortcuts="3">
                {w("fact.change")}
              </Button>
            )}
            {(Boolean(can.timelineDate && p.onStartTimelineDate) || Boolean(p.clearerPage && p.onStartMove)) && (
              <Button variant="quiet" className="rd-more-dots" aria-label={w("fact.more")} aria-expanded={moreOpen} aria-controls={`${id}-more`} onClick={() => setMoreOpen((o) => !o)}>
                <DotsIcon />
              </Button>
            )}
          </div>
          {moreOpen && (
            <div id={`${id}-more`} className="rd-actions rd-actions--quiet">
              {can.timelineDate && p.onStartTimelineDate && (
                <Button variant="quiet" disabled={p.busy} onClick={p.onStartTimelineDate}>
                  {w("fact.set_timeline_date")}
                </Button>
              )}
              {p.clearerPage && p.onStartMove && (
                <Button variant="quiet" disabled={p.busy} onClick={p.onStartMove}>
                  {w("fact.move_clearer")}
                </Button>
              )}
            </div>
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
            <Button variant="quiet" disabled={p.busy} onClick={p.onFlag}>
              {fact.flagged ? w("fact.points_against_off") : w("fact.points_against")}
            </Button>
          )}
        </>
      )}
    </article>
  );
}
