// S11 side panel: the entry the expert opened, with its page, status and questions, and the ways on (Read, or the
// day as it was seen). The timeline stays where it was; Earlier and Later step through every entry in date order.
import { useEffect, useRef, useState } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { LinkButton, PageRef, StatusBadge } from "../../foundation/ui";
import { useHighlight } from "./highlight";
import { QuestionToggle } from "./QuestionChip";
import { axisIso, entryHref, statusOf } from "./shared";
import { pageImage } from "./pageImage";
import { entryLabel, entryName } from "./TimelineBoard";
import { w } from "./words";

type Event = Schemas["Event"];

function PagePicture({ caseId, page }: { caseId: string; page: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSrc(null);
    pageImage(caseId, page).then(
      (url) => live && setSrc(url),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [caseId, page]);
  if (!src) return null;
  return <img className="av-story-panel__page" src={src} alt={w("consultation.page_alt", { page })} />;
}

export function TimelineInspector({
  caseId,
  current,
  here,
  order,
  numbers,
  onSelect,
  onClose,
}: {
  caseId: string;
  current: Event;
  /** The entries the expert opened together (a day's card or a counted mark). */
  here: Event[];
  /** Every entry on the timeline, in date order. */
  order: Event[];
  numbers?: Record<string, number>;
  onSelect: (event: Event) => void;
  onClose: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const at = order.findIndex((e) => e.fact_id === current.fact_id);
  const earlier = at > 0 ? order[at - 1] : undefined;
  const later = at >= 0 && at < order.length - 1 ? order[at + 1] : undefined;
  const chips = current.question_ids.flatMap((id) => (numbers?.[id] === undefined ? [] : [{ id, n: numbers[id]! }]));
  const { show } = useHighlight();
  const linked = current.question_ids.length;
  const dated = !!axisIso(current);

  // Closing the panel puts the screen back as it was.
  useEffect(() => () => show(null), [show]);

  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose]);

  return (
    <aside className="av-story-panel" aria-label={w("timeline.inspector.title")}>
      <div className="av-story-panel__bar">
        <PageRef page={current.page_no} />
        <span className="av-story-panel__nav">
          <button type="button" className="av-story-panel__icon" disabled={!earlier} onClick={() => earlier && onSelect(earlier)} aria-label={w("timeline.inspector.earlier")}>
            <span aria-hidden="true">‹</span>
          </button>
          <button type="button" className="av-story-panel__icon" disabled={!later} onClick={() => later && onSelect(later)} aria-label={w("timeline.inspector.later")}>
            <span aria-hidden="true">›</span>
          </button>
          <button type="button" className="av-story-panel__icon" onClick={onClose} aria-label={w("timeline.inspector.close")}>
            <span aria-hidden="true">✕</span>
          </button>
        </span>
      </div>
      <div className="av-story-panel__body">
        <h3 ref={heading} tabIndex={-1}>{entryLabel(current)}</h3>
        {current.summary && <p className="av-story-panel__summary">{current.summary}</p>}
        <div className="av-story-panel__badges">
          <StatusBadge status={statusOf(current.status)} />
          {current.has_finding && <StatusBadge status="look" />}
        </div>
        <p className="av-story-panel__linked">
          <span>{linked ? w("timeline.linked", { count: linked }) : w("timeline.not_linked")}</span>
          {chips.map((c) => (
            <QuestionToggle key={c.id} questionId={c.id} n={c.n} />
          ))}
        </p>
        {current.has_finding && <p className="av-story-panel__finding">{w("timeline.inspector.finding")}</p>}
        <PagePicture caseId={caseId} page={current.page_no} />
        <div className="av-story-panel__ways">
          <LinkButton wide to={`/cases/${caseId}/read/${current.page_no}?fact=${current.fact_id}`}>
            {w("timeline.inspector.open_read", { page: current.page_no })}
          </LinkButton>
          {dated && (
            <LinkButton wide variant="secondary" to={entryHref(caseId, current)}>
              {w("timeline.inspector.open_day")}
            </LinkButton>
          )}
        </div>
        {here.length > 1 && (
          <section className="av-story-panel__here" aria-labelledby="story-panel-here">
            <h4 id="story-panel-here">{w("timeline.inspector.here", { count: here.length })}</h4>
            <ul className="av-understand-list">
              {here.map((e) => (
                <li key={e.fact_id}>
                  <button
                    type="button"
                    className="av-story-panel__entry"
                    aria-current={e.fact_id === current.fact_id ? "true" : undefined}
                    aria-label={w("timeline.inspector.open_entry", { name: entryName(e) })}
                    onClick={() => onSelect(e)}
                  >
                    <span className="av-story-card__dot" data-state={e.has_finding ? "look" : statusOf(e.status)} aria-hidden="true" />
                    <span>{e.summary || entryLabel(e)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </aside>
  );
}
