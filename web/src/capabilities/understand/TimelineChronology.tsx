import { Link } from "react-router-dom";
import type { Schemas } from "../../foundation/platform-client";
import { PageRef, QChip, StatusBadge } from "../../foundation/ui";
import { LaneIcon } from "./laneIcons";
import type { ChronologyEntry } from "./chronologyEntries";
import { chronologyEntries } from "./chronologyEntries";
import { axisIso, entryHref, kindWord, longDate, statusOf } from "./shared";
import { w } from "./words";

type TimelineData = Pick<Schemas["Timeline"], "lanes" | "unplaced">;

export function ChronologyList({
  caseId,
  entries,
  numbers,
  headingId = "timeline-chronology-heading",
}: {
  caseId: string;
  entries: ChronologyEntry[];
  numbers?: Record<string, number>;
  headingId?: string;
}) {
  if (entries.length === 0) {
    return <p className="av-muted">{w("timeline.nothing")}</p>;
  }
  return (
    <ol className="av-understand-chronology__list" aria-labelledby={headingId}>
      {entries.map((event) => {
        const linked = event.question_ids.length;
        const chips = event.question_ids.map((id) => numbers?.[id]).filter((n): n is number => n !== undefined);
        const iso = axisIso(event);
        return (
          <li key={event.fact_id} className="av-understand-chronology__row">
            <div className="av-understand-chronology__when">
              <time dateTime={iso ?? undefined}>{iso ? longDate(iso) : w("common.no_date")}</time>
            </div>
            <div className="av-understand-chronology__main">
              {event.laneKey ? (
                <span className="av-understand-chronology__lane">
                  <span className="av-understand-lane__icon av-understand-chronology__lane-icon" aria-hidden="true">
                    <LaneIcon laneKey={event.laneKey} label={event.laneLabel} />
                  </span>
                  <span className="av-muted">{event.laneLabel}</span>
                </span>
              ) : (
                <span className="av-muted">{w("timeline.unplaced")}</span>
              )}
              <p className="av-understand-chronology__summary">
                <Link to={entryHref(caseId, event)}>{event.summary || kindWord(event.kind)}</Link>
              </p>
              <div className="av-understand-chronology__meta">
                <PageRef page={event.page_no} to={entryHref(caseId, event)} />
                <StatusBadge status={statusOf(event.status)} />
                {event.has_finding && <StatusBadge status="look" />}
                <span className="av-muted">{linked ? w("timeline.linked", { count: linked }) : w("timeline.not_linked")}</span>
                {chips.length > 0 && (
                  <span className="av-understand-chips">
                    {chips.map((n) => (
                      <QChip key={n} n={n} />
                    ))}
                  </span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function TimelineChronology({ caseId, data, numbers }: { caseId: string; data: TimelineData; numbers?: Record<string, number> }) {
  const entries = chronologyEntries(data.lanes, data.unplaced);
  return (
    <section className="av-understand-chronology" aria-labelledby="timeline-chronology-heading" data-testid="timeline-chronology">
      <h3 id="timeline-chronology-heading" className="av-visually-hidden">{w("timeline.view.chronology")}</h3>
      <ChronologyList caseId={caseId} entries={entries} numbers={numbers} />
    </section>
  );
}
