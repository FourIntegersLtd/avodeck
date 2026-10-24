// "What we found on this page" as one quiet list: a hairline-separated row for each suggestion, the
// chosen row opened in place (its fact card), the others closed. Up and down move through the rows.
import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { t } from "../../foundation/i18n";
import type { Schemas } from "../../foundation/platform-client";
import { ProgressBar } from "../../foundation/ui";
import { kindLabel, longDate } from "./data";
import { w } from "./words";

type Fact = Schemas["FactOut"];
type Mark = "please_check" | "checked" | "not_right";

const markOf = (f: Fact): Mark => (f.status === "rejected" ? "not_right" : (f.status === "verified" || f.status === "edited") && !f.needs_recheck ? "checked" : "please_check");

function StatusMark({ mark }: { mark: Mark }) {
  return (
    <span className={`rd-mark-s rd-mark-s--${mark}`} role="img" aria-label={t(`ui.status.${mark}`)}>
      {mark === "checked" && (
        <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12.5l4.5 4.5L19 7" />
        </svg>
      )}
      {mark === "not_right" && (
        <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      )}
    </span>
  );
}

export function FactList({
  pack,
  facts,
  focus,
  onFocus,
  children,
}: {
  pack: Schemas["CasePack"];
  facts: Fact[];
  focus: string | null;
  onFocus: (id: string) => void;
  /** The fact card for the open row. */
  children: (fact: Fact) => ReactNode;
}) {
  const list = useRef<HTMLUListElement>(null);
  const done = facts.filter((f) => markOf(f) !== "please_check").length;
  const kinds = Array.from(new Set(facts.map((f) => f.kind)));
  const groups = kinds.map((kind) => ({ kind, facts: facts.filter((f) => f.kind === kind) }));
  const order = groups.flatMap((g) => g.facts);

  function onKey(e: KeyboardEvent<HTMLUListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const from = (e.target as HTMLElement).closest<HTMLElement>("[data-fact]");
    if (!(e.target as HTMLElement).closest(".rd-row__head") || !from) return;
    e.preventDefault();
    const to = order[order.findIndex((f) => f.id === from.dataset.fact) + (e.key === "ArrowDown" ? 1 : -1)];
    if (!to) return;
    onFocus(to.id);
    list.current?.querySelector<HTMLElement>(`[data-fact="${to.id}"] .rd-row__head`)?.focus();
  }

  const row = (f: Fact) => {
    const open = f.id === focus;
    const mark = markOf(f);
    const date = longDate(f.event_date);
    return (
      <li key={f.id} data-fact={f.id} className={["rd-row", open && "rd-row--open", mark !== "please_check" && "rd-row--done"].filter(Boolean).join(" ")}>
        <button type="button" className="rd-row__head" aria-expanded={open} onClick={() => onFocus(f.id)}>
          <StatusMark mark={mark} />
          <span className="rd-row__text">
            <span className="rd-row__meta">
              <span>{kindLabel(pack, f.kind)}</span>
              {date && " · "}
              {date && <span>{date}</span>}
            </span>
            <span className="rd-row__words">{`“${f.value ?? ""}”`}</span>
          </span>
        </button>
        {open && children(f)}
      </li>
    );
  };

  return (
    <div className="rd-list">
      <div className="rd-list__count">
        <p>{w("read.checked_of", { done, total: facts.length })}</p>
        <div className="rd-list__prog">
          <ProgressBar value={done} max={facts.length || 1} label={w("read.checked_of", { done, total: facts.length })} />
        </div>
      </div>
      <ul ref={list} className="rd-list__rows" aria-label={w("read.found")} onKeyDown={onKey}>
        {groups.length > 1
          ? groups.flatMap((g) => [
              <li key={`h-${g.kind}`} className="rd-group">
                <h3 className="rd-group__head">{`${kindLabel(pack, g.kind)} (${g.facts.length})`}</h3>
              </li>,
              ...g.facts.map(row),
            ])
          : facts.map(row)}
      </ul>
    </div>
  );
}
