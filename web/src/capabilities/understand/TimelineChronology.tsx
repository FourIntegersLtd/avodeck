// S11 List: every entry in the window in date order, under month headings. Each row opens the same side panel as
// the board, so the two views differ only in how the entries are laid out.
import type { Schemas } from "../../foundation/platform-client";
import { PageRef, StatusBadge } from "../../foundation/ui";
import { HighlightRow, QuestionChip } from "./QuestionChip";
import { LaneIcon } from "./laneIcons";
import type { ChronologyEntry } from "./chronologyEntries";
import { axisIso, kindWord, statusOf } from "./shared";
import { entryName } from "./TimelineBoard";
import { w } from "./words";

type Event = Schemas["Event"];

const MONTH_YEAR = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

function byMonth(entries: ChronologyEntry[]): { key: string; title: string; rows: ChronologyEntry[] }[] {
  const out: { key: string; title: string; rows: ChronologyEntry[] }[] = [];
  for (const e of entries) {
    const iso = axisIso(e);
    const key = iso ? iso.slice(0, 7) : "none";
    let group = out.at(-1);
    if (!group || group.key !== key) {
      group = { key, title: iso ? MONTH_YEAR.format(new Date(`${key}-01T00:00:00Z`)) : w("common.no_date"), rows: [] };
      out.push(group);
    }
    group.rows.push(e);
  }
  return out;
}

export function TimelineChronology({
  entries,
  numbers,
  selectedId,
  onOpen,
}: {
  entries: ChronologyEntry[];
  numbers?: Record<string, number>;
  selectedId?: string | null;
  onOpen: (events: Event[], at?: string) => void;
}) {
  if (entries.length === 0) return <p className="av-muted">{w("timeline.nothing")}</p>;
  return (
    <section className="av-understand-chronology" data-testid="timeline-chronology" aria-label={w("timeline.view.list")}>
      {byMonth(entries).map((month) => (
        <section key={month.key} className="av-story-month" aria-labelledby={`month-${month.key}`}>
          <h4 id={`month-${month.key}`} className="av-story-month__head">
            {month.title}
          </h4>
          <ol className="av-understand-chronology__list">
            {month.rows.map((event) => {
              const iso = axisIso(event);
              const chips = event.question_ids.flatMap((id) => (numbers?.[id] === undefined ? [] : [{ id, n: numbers[id]! }]));
              return (
                <HighlightRow key={event.fact_id} questionIds={event.question_ids} className={`av-understand-chronology__row${selectedId === event.fact_id ? " av-understand-chronology__row--selected" : ""}`}>
                  <div className="av-understand-chronology__when">
                    <time dateTime={iso ?? undefined}>{iso ? SHORT_DAY.format(new Date(`${iso}T00:00:00Z`)) : w("common.no_date")}</time>
                  </div>
                  <div className="av-understand-chronology__main">
                    {event.laneKey ? (
                      <span className="av-understand-chronology__lane">
                        <span className="av-understand-lane__icon av-understand-chronology__lane-icon" aria-hidden="true">
                          <LaneIcon icon={event.laneIcon} label={event.laneLabel} />
                        </span>
                        <span className="av-muted">{event.laneLabel}</span>
                      </span>
                    ) : (
                      <span className="av-muted">{w("timeline.unplaced")}</span>
                    )}
                    <p className="av-understand-chronology__summary">
                      <button type="button" className="av-story-list__open" data-facts={event.fact_id} aria-label={w("timeline.list.open", { name: entryName(event) })} onClick={() => onOpen([event], event.fact_id)}>
                        {event.summary || kindWord(event.kind)}
                      </button>
                    </p>
                    <div className="av-understand-chronology__meta">
                      <PageRef page={event.page_no} />
                      <StatusBadge status={statusOf(event.status)} />
                      {event.has_finding && <StatusBadge status="look" />}
                      {chips.length > 0 && (
                        <span className="av-understand-chips">
                          {chips.map((c) => (
                            <QuestionChip key={c.id} questionId={c.id} n={c.n} />
                          ))}
                        </span>
                      )}
                    </div>
                  </div>
                </HighlightRow>
              );
            })}
          </ol>
        </section>
      ))}
    </section>
  );
}
