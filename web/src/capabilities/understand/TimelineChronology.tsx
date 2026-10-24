// S11 List: every entry in the window in date order, under month headings. Each row opens the same side panel as
// the board, so the two views differ only in how the entries are laid out.
import type { Schemas } from "../../foundation/platform-client";
import { ListRow, StatusMark } from "../../foundation/ui";
import { useHighlightAttrs } from "./highlight";
import type { ChronologyEntry } from "./chronologyEntries";
import { axisIso, statusOf, useKindWord } from "./shared";
import { entryLabel } from "./TimelineBoard";
import { w } from "./words";

type Event = Schemas["Event"];

const MONTH_YEAR = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

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

function Row({ event, selected, onOpen }: { event: ChronologyEntry; selected: boolean; onOpen: (events: Event[], at?: string) => void }) {
  const word = useKindWord();
  const attrs = useHighlightAttrs(event.question_ids);
  const where = event.laneLabel ? event.laneLabel : w("timeline.unplaced");
  return (
    <div className="av-calm-row" data-facts={event.fact_id} {...attrs}>
      <ul className="av-calm-rows">
        <ListRow
          mark={<StatusMark status={event.has_finding ? "look" : statusOf(event.status)} />}
          title={w("timeline.list.title", { label: entryLabel(event, word), where, page: event.page_no })}
          quote={event.summary || undefined}
          expanded={selected}
          onToggle={() => onOpen([event], event.fact_id)}
        />
      </ul>
    </div>
  );
}

export function TimelineChronology({
  entries,
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
    <section className="av-understand-chronology av-calm-card" data-testid="timeline-chronology" aria-label={w("timeline.view.list")}>
      {byMonth(entries).map((month) => (
        <section key={month.key} className="av-story-month" aria-labelledby={`month-${month.key}`}>
          <h4 id={`month-${month.key}`} className="av-story-month__head">
            {month.title}
          </h4>
          {month.rows.map((event) => (
            <Row key={event.fact_id} event={event} selected={selectedId === event.fact_id} onOpen={onOpen} />
          ))}
        </section>
      ))}
    </section>
  );
}
