// Screen 9b, the opening view of the transcript (ADR 0028): what the transcript holds and where to start.
// It shows no entry text, so opening it never counts anything as read.
import { Link } from "react-router-dom";
import { humanise, t } from "../../foundation/i18n";
import { formatDay } from "../../foundation/i18n/dates";
import type { Schemas } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { count } from "./data";
import { w } from "./words";

export type Section = Schemas["OutlineSection"];
export type Coverage = NonNullable<Schemas["TranscriptOutline"]["coverage"]>;

/** The two lists after the sections: pages with no entries, and pages the computer couldn't read. */
export const NO_ENTRIES = "_no_entries";
export const UNREAD = "_unread";

export interface Appendix {
  key: typeof NO_ENTRIES | typeof UNREAD;
  /** "Appendix A · Pages with no entries"; the letters follow the files, so an absent first list leaves A for the next. */
  title: string;
  count: number;
}

export function appendices(pictureCount: number, unreadCount: number): Appendix[] {
  const list: Appendix[] = [];
  const letter = () => String.fromCharCode(65 + list.length);
  if (pictureCount > 0) list.push({ key: NO_ENTRIES, title: w("transcript.appendix_a", { letter: letter() }), count: pictureCount });
  if (unreadCount > 0) list.push({ key: UNREAD, title: w("transcript.appendix_b", { letter: letter() }), count: unreadCount });
  return list;
}

export const sectionLabel = (section: Section) => t(section.label, {}, humanise(section.key));

export const entriesText = (n: number, locale: string | null | undefined) => w("transcript.entries_count", { count: n, shown: count(n, locale) });
export const pagesText = (n: number, locale: string | null | undefined) => w("transcript.pages_count", { count: n, shown: count(n, locale) });

/** "Mar 2015 – Jun 2023"; empty when the section has no dated entry. */
export function dateRange(section: Section): string {
  if (!section.first_date || !section.last_date) return "";
  const from = formatDay(section.first_date, undefined, "monthYearShort");
  const to = formatDay(section.last_date, undefined, "monthYearShort");
  return from === to ? from : w("transcript.date_range", { from, to });
}

/** The address of a view: the contents when `key` is null. */
export const viewSearch = (key: string | null) => (key ? `?section=${encodeURIComponent(key)}` : "");

const CHEVRON = (
  <svg className="rd-contents__chevron" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 5l7 7-7 7" />
  </svg>
);

interface Props {
  coverage?: Coverage;
  sections: Section[];
  appendices: Appendix[];
  locale: string | null | undefined;
  /** Asks again about the pages the computer could not read; left out when this person may not start the work. */
  onRetryUnread?: () => void;
}

export function TranscriptContents({ coverage, sections, appendices: lists, locale, onRetryUnread }: Props) {
  const tiles = coverage
    ? [
        { label: w("transcript.tiles.total"), n: coverage.total_pages, tone: "mint" },
        { label: w("transcript.tiles.with"), n: coverage.with_entries, tone: "mint" },
        { label: w("transcript.tiles.none"), n: coverage.no_entries, tone: coverage.no_entries > 0 ? "amber" : "mint" },
        { label: w("transcript.tiles.unread"), n: coverage.unreadable, tone: coverage.unreadable > 0 ? "amber" : "mint" },
      ]
    : [];
  return (
    <>
      <section className="rd-about" aria-labelledby="transcript-view-heading">
        <h2 id="transcript-view-heading" tabIndex={-1} className="rd-view-heading">{w("transcript.about")}</h2>
        <p>{w("transcript.about_lead")}</p>
        {tiles.length > 0 && (
          <dl className="rd-tiles">
            {tiles.map((tile) => (
              <div key={tile.label} className={`rd-tile rd-tile--${tile.tone}`}>
                <dt>{tile.label}</dt>
                <dd>{count(tile.n, locale)}</dd>
              </div>
            ))}
          </dl>
        )}
        {onRetryUnread && (
          <div className="rd-tiles__note">
            <p>{w("transcript.try_unread_lead")}</p>
            <Button variant="secondary" onClick={onRetryUnread}>{w("transcript.try_unread")}</Button>
          </div>
        )}
        {coverage && coverage.set_aside > 0 && <p className="rd-tiles__note">{w("transcript.set_aside_note", { count: coverage.set_aside, shown: count(coverage.set_aside, locale) })}</p>}
      </section>
      <section aria-labelledby="transcript-contents">
        <h2 id="transcript-contents" className="rd-view-heading">{w("transcript.contents")}</h2>
        {sections.length > 0 && (
          <ol className="rd-contents">
            {sections.map((section, i) => {
              const range = dateRange(section);
              return (
                <li key={section.key}>
                  <Link className="rd-contents__row" to={{ search: viewSearch(section.key) }}>
                    <span className="rd-contents__num">{i + 1}</span>
                    <span className="rd-contents__main">
                      <span className="rd-contents__label">{sectionLabel(section)}</span>
                      <span className="rd-contents__meta">{range ? `${entriesText(section.count, locale)} · ${range}` : entriesText(section.count, locale)}</span>
                    </span>
                    {CHEVRON}
                  </Link>
                </li>
              );
            })}
          </ol>
        )}
        {lists.length > 0 && (
          <ul className="rd-contents rd-contents--appendix">
            {lists.map((list) => (
              <li key={list.key}>
                <Link className="rd-contents__row rd-contents__row--amber" to={{ search: viewSearch(list.key) }}>
                  <span className="rd-contents__main">
                    <span className="rd-contents__label">{w("transcript.appendix_row", { title: list.title, shown: count(list.count, locale) })}</span>
                  </span>
                  {CHEVRON}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
