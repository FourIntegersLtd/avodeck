// Moves between suggestions on a dense page without stacking every card in the column.
import type { Schemas } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { kindLabel } from "./data";
import { w } from "./words";

type Fact = Schemas["FactOut"];

function summary(fact: Fact): string {
  const kind = kindLabel(fact.kind);
  const value = fact.value?.trim() ?? "";
  return value.length > 48 ? `${kind}: ${value.slice(0, 45)}…` : `${kind}: ${value}`;
}

function actionable(list: Fact[]): Fact[] {
  return list.filter((f) => f.status === "suggested" || (f.status === "verified" && f.needs_recheck) || (f.status === "edited" && f.needs_recheck));
}

export function FactSwitcher({
  facts,
  focus,
  onFocus,
}: {
  facts: Fact[];
  focus: string | null;
  onFocus: (id: string) => void;
}) {
  if (facts.length <= 1) return null;
  const queue = actionable(facts);
  const index = Math.max(0, queue.findIndex((f) => f.id === focus));
  const current = queue[index] ?? queue[0];
  const prev = queue.length > 1 ? queue[(index - 1 + queue.length) % queue.length] : current;
  const next = queue.length > 1 ? queue[(index + 1) % queue.length] : current;

  const done = facts.filter((f) => (f.status === "verified" || f.status === "edited") && !f.needs_recheck);
  const rejected = facts.filter((f) => f.status === "rejected");

  return (
    <div className="rd-switcher">
      {queue.length > 1 && (
        <div className="rd-switcher__nav">
          <Button variant="secondary" aria-label={w("read.suggestion_prev")} onClick={() => onFocus(prev!.id)}>
            ←
          </Button>
          <p className="rd-switcher__count" aria-live="polite">
            {w("read.suggestion_of", { n: index + 1, total: queue.length })}
          </p>
          <Button variant="secondary" aria-label={w("read.suggestion_next")} onClick={() => onFocus(next!.id)}>
            →
          </Button>
        </div>
      )}
      {queue.length > 0 && (
        <ul className="rd-switcher__list">
          {queue.map((f) => (
            <li key={f.id}>
              <button type="button" className={f.id === current?.id ? "rd-switcher__item rd-switcher__item--on" : "rd-switcher__item"} aria-current={f.id === focus} onClick={() => onFocus(f.id)}>
                {summary(f)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {(done.length > 0 || rejected.length > 0) && (
        <details className="rd-switcher__done">
          <summary>{w("read.checked_on_page", { count: done.length + rejected.length })}</summary>
          <ul className="rd-switcher__list">
            {[...done, ...rejected].map((f) => (
              <li key={f.id}>
                <button type="button" className="rd-switcher__item rd-switcher__item--quiet" onClick={() => onFocus(f.id)}>
                  {summary(f)}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
