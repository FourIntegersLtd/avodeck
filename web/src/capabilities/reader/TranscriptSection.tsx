// Screen 9b, one section of the transcript (ADR 0028): its entries under year and month headings, fifty at a time.
// Moving about (a year, the next fifty, the next section) records nothing as read; only entries on screen do.
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { safeLocale } from "../../foundation/i18n/dates";
import type { Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Menu, MenuItem, MenuSection } from "../../foundation/ui";
import { count } from "./data";
import { dateRange, entriesText, pagesText, sectionLabel, viewSearch, type Appendix, type Section } from "./TranscriptContents";
import { w } from "./words";

type Entry = Schemas["TranscriptEntryOut"];
type Year = Schemas["OutlineYear"];

/** How many entries one press of "Show the next" brings. */
export const PAGE_SIZE = 50;

const yearOf = (entry: Entry) => (entry.event_date ? Number(entry.event_date.slice(0, 4)) : null);
const monthOf = (entry: Entry) => (entry.event_date ? Number(entry.event_date.slice(5, 7)) : null);
export const yearId = (year: number | null) => `year-${year ?? "none"}`;

function monthName(month: number): string {
  return new Intl.DateTimeFormat(safeLocale(undefined), { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2000, month - 1, 15)));
}

interface RailProps {
  sections: Section[];
  appendices: Appendix[];
  current: string;
  locale: string | null | undefined;
  onYear: (year: Year) => void;
  years: Year[];
}

/** The same contents as the opening view, compact, beside the section on a wide screen. */
function Rail({ sections, appendices, current, locale, onYear, years }: RailProps) {
  return (
    <nav className="rd-rail" aria-label={w("transcript.contents")}>
      <ol>
        {sections.map((section, i) => {
          const here = section.key === current;
          return (
            <li key={section.key}>
              <Link className="rd-rail__row" aria-current={here ? "true" : undefined} to={{ search: viewSearch(section.key) }}>
                <span className="rd-contents__num">{i + 1}</span>
                <span>{sectionLabel(section)}</span>
              </Link>
              {here && years.length > 0 && (
                <ul className="rd-rail__years">
                  {years.map((year) => (
                    <li key={year.offset}>
                      <button type="button" className="rd-rail__year" onClick={() => onYear(year)}>
                        {year.year ?? w("transcript.undated_heading")}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
      {appendices.length > 0 && (
        <ul>
          {appendices.map((list) => (
            <li key={list.key}>
              <Link className="rd-rail__row rd-rail__row--amber" aria-current={list.key === current ? "true" : undefined} to={{ search: viewSearch(list.key) }}>
                {w("transcript.appendix_row", { title: list.title, shown: count(list.count, locale) })}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

interface Props {
  section: Section;
  number: number;
  sections: Section[];
  appendices: Appendix[];
  /** The entries loaded so far, in the server's order; null while the first fifty are on their way. */
  entries: Entry[] | null;
  locale: string | null | undefined;
  renderEntry: (entry: Entry, level: 4 | 5) => ReactNode;
  onMore: () => void;
  /** Entries are on their way: the button waits, so one press asks once. */
  busy?: boolean;
  /** The last request for more entries failed; Try again repeats it. */
  moreFailed?: boolean;
  onRetryMore?: () => void;
  onYear: (year: Year) => void;
  go: (key: string | null) => void;
}

export function TranscriptSection({ section, number, sections, appendices: lists, entries, locale, renderEntry, onMore, busy, moreFailed, onRetryMore, onYear, go }: Props) {
  const range = dateRange(section);
  const facts = range
    ? w("transcript.section_facts_dated", { entries: entriesText(section.count, locale), pages: pagesText(section.pages, locale), range })
    : w("transcript.section_facts", { entries: entriesText(section.count, locale), pages: pagesText(section.pages, locale) });
  const position = sections.findIndex((s) => s.key === section.key);
  const following = sections[position + 1];
  const nextLabel = following ? sectionLabel(following) : lists[0]?.title;
  const nextKey = following ? following.key : lists[0]?.key;

  const body: ReactNode[] = [];
  let lastYear: number | null | undefined;
  let lastMonth: number | null | undefined;
  for (const entry of entries ?? []) {
    const year = yearOf(entry);
    const month = monthOf(entry);
    if (year !== lastYear) {
      body.push(
        <h3 key={`y-${body.length}`} id={yearId(year)} tabIndex={-1} className="rd-year-heading">
          {year ?? w("transcript.undated_heading")}
        </h3>,
      );
      lastYear = year;
      lastMonth = undefined;
    }
    if (month !== null && month !== lastMonth) {
      body.push(
        <h4 key={`m-${body.length}`} className="rd-month-heading">
          {monthName(month)}
        </h4>,
      );
      lastMonth = month;
    }
    body.push(<Fragment key={entry.id}>{renderEntry(entry, month === null ? 4 : 5)}</Fragment>);
  }

  const shown = entries?.length ?? 0;
  const step = Math.min(PAGE_SIZE, section.count - shown);
  const switcher = (
    <Menu label={w("transcript.contents")} trigger={w("transcript.contents_button")}>
      <MenuSection>
        {sections.map((s, i) => (
          <MenuItem key={s.key} onClick={() => go(s.key)} pressed={s.key === section.key}>
            {`${i + 1}. ${sectionLabel(s)}`}
          </MenuItem>
        ))}
        {lists.map((list) => (
          <MenuItem key={list.key} onClick={() => go(list.key)}>
            {list.title}
          </MenuItem>
        ))}
      </MenuSection>
    </Menu>
  );

  return (
    <div className="rd-section-layout">
      <Rail sections={sections} appendices={lists} current={section.key} locale={locale} years={section.years} onYear={onYear} />
      <div className="rd-section">
        <div className="rd-section__top">
          <Link className="rd-back" to={{ search: viewSearch(null) }}>{w("transcript.back_link")}</Link>
          <div className="rd-section__switch">{switcher}</div>
        </div>
        <h2 id="transcript-view-heading" tabIndex={-1} className="rd-view-heading">
          {w("transcript.section_title", { number, label: sectionLabel(section) })}
        </h2>
        <p className="rd-section__facts">{facts}</p>
        {section.years.length > 0 && (
          <div role="group" aria-label={w("transcript.years")} className="rd-years">
            {section.years.map((year) => (
              <button key={year.offset} type="button" className="rd-year" onClick={() => onYear(year)}>
                {year.year ?? w("transcript.undated_heading")}
              </button>
            ))}
          </div>
        )}
        {entries === null ? <Loading /> : body}
        {moreFailed && onRetryMore && <LoadError onRetry={onRetryMore} />}
        {entries !== null && step > 0 && (
          <Button variant="secondary" busy={busy} onClick={onMore}>
            {w("transcript.show_next", { step: count(step, locale), from: count(shown + 1, locale), to: count(shown + step, locale), total: count(section.count, locale) })}
          </Button>
        )}
        <div className="rd-section__end">
          {nextKey && nextLabel && (
            <LinkButton to={{ search: viewSearch(nextKey) }} arrow>
              {w("transcript.next_section", { label: nextLabel })}
            </LinkButton>
          )}
          <LinkButton variant="secondary" to={{ search: viewSearch(null) }}>
            {w("transcript.back_to_contents")}
          </LinkButton>
        </div>
      </div>
    </div>
  );
}
