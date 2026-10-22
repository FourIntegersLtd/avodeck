// S11 the board: one row per pack lane across the chosen window. Each day's entries in a lane share one card; the
// cards that matter most get the room, and the rest are drawn as marks and counted, never dropped. Opening a card,
// a count or an undated entry hands its entries to the side panel; the board itself never navigates.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "../../foundation/i18n";
import { screenPath } from "../../foundation/lib/screens";
import type { Schemas } from "../../foundation/platform-client";
import { LinkButton, PageRef, QChip } from "../../foundation/ui";
import { CARD, CHIP_ROW, GAP, HEAD, ROW } from "./boardGeometry";
import { GapConnectors } from "./GapConnectors";
import { useHighlightAttrs } from "./highlight";
import { QuestionChip } from "./QuestionChip";
import { SeriesBars, seriesFacts, seriesRows } from "./SeriesBars";
import { useCardMotion } from "./useCardMotion";
import { LaneIcon } from "./laneIcons";
import { axisIso, kindWord, longDate } from "./shared";
import { dayMs, foldChips, groupByDay, layoutLane, levelFor, monthlyCounts, type Level, type StoryGroup } from "./storyLayout";
import { w } from "./words";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];
type Source = Schemas["TimelineSource"];

export type BoardData = { lanes: Lane[]; unplaced: Event[]; sources?: Source[]; counts?: Schemas["TimelineCounts"] | null; range?: { first: string; last: string } | null; gaps?: Schemas["Gap"][] | null };

const DEFAULT_WIDTH = 960;

const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const MONTH = (year: boolean) => new Intl.DateTimeFormat("en-GB", { month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" });
const NUMBER = new Intl.NumberFormat("en-GB");
const shortDay = (iso: string) => SHORT_DAY.format(new Date(`${iso}T00:00:00Z`));

/** "Result · 19 February 2024": what an entry is called everywhere on this screen. */
export const entryLabel = (e: Event) => w("timeline.card_label", { kind: kindWord(e.kind), date: longDate(axisIso(e)) });
export const entryName = (e: Event) => (e.summary ? w("timeline.card_name", { label: entryLabel(e), words: e.summary }) : entryLabel(e));

/** Splits lane entries without a date for the axis out to the "not on a line yet" list. */
export function splitForTrack(data: { lanes: Lane[]; unplaced: Event[] }) {
  const unplaced = [...data.unplaced];
  const lanes = data.lanes.map((lane) => {
    const onTrack: Event[] = [];
    for (const e of lane.events) {
      if (axisIso(e)) onTrack.push(e);
      else unplaced.push(e);
    }
    return { ...lane, events: onTrack };
  });
  return { lanes, unplaced };
}

/** The window shown: the chosen dates, else the stretch the entries cover. */
function domainOf(from: string, to: string, lanes: Lane[], range?: { first: string; last: string } | null) {
  let first = from;
  let last = to;
  if (!first || !last) {
    const days = lanes.flatMap((l) => l.events.map(axisIso)).filter((d): d is string => !!d).sort();
    first ||= range?.first ?? days[0] ?? "";
    last ||= range?.last ?? days.at(-1) ?? "";
  }
  if (!first || !last) return null;
  const start = dayMs(`${first.slice(0, 7)}-01`);
  const [y, m] = last.split("-").map(Number);
  const end = Date.UTC(y!, m!, 1);
  return { start, end, span: Math.max(end - start, 86_400_000), first, last };
}

function axisMarks(start: number, end: number, level: Level): { at: number; label: string }[] {
  const marks: { at: number; label: string }[] = [];
  const total = Math.round((end - start) / (30.44 * 86_400_000));
  const step = level === "years" ? 12 : total > 12 ? 3 : 1;
  for (let d = new Date(start), i = 0; d.getTime() < end; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)), i++) {
    const january = d.getUTCMonth() === 0;
    if (level === "years" ? !(january || i === 0) : !(i % step === 0 || january)) continue;
    marks.push({ at: d.getTime(), label: level === "years" ? String(d.getUTCFullYear()) : MONTH(i === 0 || january).format(d) });
  }
  return marks;
}

function useWidth(ref: React.RefObject<HTMLElement>) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setWidth(el.clientWidth || DEFAULT_WIDTH);
    read();
    if (typeof ResizeObserver === "undefined") return;
    const seen = new ResizeObserver(read);
    seen.observe(el);
    return () => seen.disconnect();
  }, [ref]);
  return width;
}

function GroupCard({ group, x, row, numbers, selected, onOpen }: { group: StoryGroup; x: number; row: number; numbers?: Record<string, number>; selected: boolean; onOpen: (events: Event[], at?: string) => void }) {
  const lead = group.events[0]!;
  const many = group.events.length;
  const chips = group.questionIds.flatMap((id) => (numbers?.[id] === undefined ? [] : [{ id, n: numbers[id]! }]));
  const highlight = useHighlightAttrs(group.questionIds);
  // The name is the words on the card, led by a fuller label for screen readers.
  return (
    <button
      type="button"
      className={`av-story-card${selected ? " av-story-card--selected" : ""}`}
      style={{ left: x, top: row * ROW + 4, width: CARD }}
      data-state={group.state}
      data-facts={group.events.map((e) => e.fact_id).join(" ")}
      {...highlight}
      onClick={() => onOpen(group.events, lead.fact_id)}
    >
      <span className="av-visually-hidden">{`${entryLabel(lead)}: `}</span>
      <span className="av-story-card__dot" data-state={group.state} aria-hidden="true" />
      <span className="av-story-card__words">
        <span className="av-story-card__top">
          <strong>{shortDay(group.date)}</strong>
          {chips.map((c) => (
            <QuestionChip key={c.id} questionId={c.id} n={c.n} />
          ))}
        </span>
        <small>
          {many > 1 && <span>{w("timeline.lane_count", { count: many })}</span>}
          {many > 1 && lead.summary && " · "}
          {(lead.summary || many === 1) && <span>{lead.summary || kindWord(lead.kind)}</span>}
        </small>
      </span>
    </button>
  );
}

type LaneLayout = { cards: { group: StoryGroup; x: number; row: number }[]; folded: { group: StoryGroup; x: number }[]; chips: ReturnType<typeof foldChips> };

/** Far away only what matters most gets a card; everything else is a counted mark. */
function layoutFor(groups: StoryGroup[], level: Level, rows: number, track: number, toX: (iso: string) => number): LaneLayout {
  const eligible = level === "years" ? groups.filter((g) => g.rank <= 1) : groups;
  const placed = layoutLane(eligible, { width: track, toX, card: CARD, gap: GAP, rows });
  const rest = level === "years" ? groups.filter((g) => g.rank > 1).map((group) => ({ group, x: toX(group.date) })) : [];
  const folded = [...placed.folded, ...rest];
  return { cards: placed.cards, folded, chips: foldChips(folded, CARD / 2) };
}

function LaneRow({
  lane,
  layout,
  all,
  gaps,
  track,
  toX,
  numbers,
  selectedId,
  onOpen,
}: {
  lane: Lane;
  layout: LaneLayout;
  all: StoryGroup[];
  gaps: Schemas["Gap"][];
  track: number;
  toX: (iso: string) => number;
  numbers?: Record<string, number>;
  selectedId?: string | null;
  onOpen: (events: Event[], at?: string) => void;
}) {
  const reserve = seriesRows(lane);
  const count = lane.events.length;
  const used = layout.cards.length ? Math.max(...layout.cards.map((c) => c.row)) + 1 : 0;
  const height = Math.max((reserve + used) * ROW + 8, 52) + (layout.chips.length ? CHIP_ROW : 0);
  const ticks = layout.folded.map((f) => `M${f.x.toFixed(1)} 0v10`).join("");
  const headingId = `lane-${lane.key}`;
  return (
    <section aria-labelledby={headingId} className="av-story-lane">
      <div className="av-story-lane__head">
        <span className="av-understand-lane__icon" data-testid="lane-icon" aria-hidden="true" style={{ background: lane.colour ?? "var(--ink-soft)" }}>
          <LaneIcon icon={lane.icon} label={lane.label} />
        </span>
        <span>
          <h3 id={headingId}>{lane.label}</h3>
          <span className="av-story-lane__count">{count ? (lane.subtitle ?? w("timeline.lane_window", { count })) : w("timeline.empty_window")}</span>
        </span>
      </div>
      <div className="av-story-lane__track" style={{ height: count ? height : 52 }}>
        {count === 0 ? null : (
          <>
            {ticks && (
              <svg className="av-story-lane__ticks" width={track} height="10" aria-hidden="true" focusable="false">
                <path d={ticks} />
              </svg>
            )}
            <SeriesBars lane={lane} toX={toX} track={track} onOpen={onOpen} />
            <GapConnectors cards={layout.cards} all={all} gaps={gaps} toX={toX} rowOffset={reserve} />
            {layout.cards.map((c) => (
              <GroupCard key={c.group.key} group={c.group} x={c.x} row={reserve + c.row} numbers={numbers} selected={!!selectedId && c.group.events.some((e) => e.fact_id === selectedId)} onOpen={onOpen} />
            ))}
            {layout.chips.map((chip) => {
              const events = chip.groups.flatMap((g) => g.events);
              const near = longDate(chip.groups[0]!.date);
              return (
                <button
                  key={chip.groups[0]!.key}
                  type="button"
                  className="av-story-more"
                  style={{ left: Math.max(0, Math.min(chip.x, track - 64)), top: (reserve + used) * ROW + 8 }}
                  data-facts={events.map((e) => e.fact_id).join(" ")}
                  onClick={() => onOpen(events)}
                >
                  <span className="av-visually-hidden">{w("timeline.more", { count: chip.count, date: near })}</span>
                  <span aria-hidden="true">{w("timeline.more_short", { count: chip.count })}</span>
                </button>
              );
            })}
          </>
        )}
      </div>
    </section>
  );
}

export function TimelineBoard({
  caseId,
  data,
  from,
  to,
  numbers,
  selectedId,
  onOpen,
}: {
  caseId: string;
  data: BoardData;
  from: string;
  to: string;
  numbers?: Record<string, number>;
  selectedId?: string | null;
  onOpen: (events: Event[], at?: string) => void;
}) {
  const boardRef = useRef<HTMLDivElement>(null);
  const width = useWidth(boardRef);
  const track = Math.max(320, width - (width <= 720 ? 140 : HEAD) - 24);
  const { lanes, unplaced } = useMemo(() => splitForTrack(data), [data]);
  const domain = domainOf(from, to, lanes, data.range);
  const level: Level = domain ? levelFor(domain.first, domain.last) : "months";
  const toX = useMemo(() => (domain ? (iso: string) => ((dayMs(iso) - domain.start) / domain.span) * track : () => 0), [domain?.start, domain?.span, track]); // eslint-disable-line react-hooks/exhaustive-deps
  // Entries drawn on a series bar get no card of their own.
  const byDay = useMemo(
    () =>
      lanes.map((lane) => {
        const onBar = seriesFacts(lane);
        return { lane, groups: groupByDay(lane.key, onBar.size ? lane.events.filter((e) => !onBar.has(e.fact_id)) : lane.events) };
      }),
    [lanes],
  );
  const all = useMemo(() => byDay.flatMap((g) => g.groups), [byDay]);
  const busy = byDay.filter((g) => g.groups.length > 0).length;
  const rows = level === "years" ? 1 : level === "days" || busy < 7 ? 3 : 2;
  const laid = useMemo(() => byDay.map((g) => ({ ...g, layout: layoutFor(g.groups, level, rows, track, toX) })), [byDay, level, rows, track, toX]);
  // Entries on a series bar are on screen too, so they count as shown and in the total.
  const onBars = lanes.reduce((n, lane) => n + seriesFacts(lane).size, 0);
  const total = all.reduce((n, g) => n + g.events.length, 0) + onBars;
  const shown = laid.reduce((n, l) => n + l.layout.cards.reduce((s, c) => s + c.group.events.length, 0), 0) + onBars;
  const marks = domain ? axisMarks(domain.start, domain.end, level) : [];
  const density = useMemo(() => (domain && level === "years" ? monthlyCounts(lanes, domain.first, domain.last) : null), [domain?.first, domain?.last, level, lanes]); // eslint-disable-line react-hooks/exhaustive-deps
  const found = data.counts?.things_to_look ?? 0;

  useCardMotion(boardRef, `${from}|${to}`);

  useEffect(() => {
    if (!selectedId) return;
    boardRef.current?.querySelector(`[data-facts~="${selectedId}"]`)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selectedId]);

  return (
    <div className="av-stack">
      <div className="av-story-board" data-testid="timeline-board" data-level={level} ref={boardRef}>
        <div className="av-story-axis">
          <span className="av-story-axis__level">{w(`timeline.level.${level}`)}</span>
          {domain && (
            <ul className="av-story-axis__marks" aria-label={w(level === "years" ? "timeline.years" : "timeline.months")} style={{ width: track }}>
              {marks.map((m) => (
                <li key={m.at} style={{ left: ((m.at - domain.start) / domain.span) * track }}>
                  {m.label}
                </li>
              ))}
            </ul>
          )}
        </div>
        {laid.map(({ lane, groups, layout }) => (
          <div key={lane.key} className="av-story-lane-wrap">
            {density && groups.length > 0 && (
              <svg className="av-story-density" width={track} height="24" aria-hidden="true" focusable="false">
                <path
                  fill={lane.colour ?? "var(--ink-soft)"}
                  d={density.byLane[lane.key]!.map((n, i) => {
                    if (!n) return "";
                    const max = Math.max(...density.byLane[lane.key]!);
                    const x = (i / density.months.length) * track;
                    const h = Math.max(2, (n / max) * 22);
                    return `M${x.toFixed(1)} 24v-${h.toFixed(1)}h${Math.max(1.5, track / density.months.length - 1).toFixed(1)}v${h.toFixed(1)}z`;
                  }).join("")}
                />
              </svg>
            )}
            <LaneRow lane={lane} layout={layout} all={all} gaps={data.gaps ?? []} track={track} toX={toX} numbers={numbers} selectedId={selectedId} onOpen={onOpen} />
          </div>
        ))}
      </div>
      {unplaced.length > 0 && (
        <section aria-labelledby="timeline-unplaced" className="av-card av-story-unplaced">
          <h3 id="timeline-unplaced">{w("timeline.unplaced")}</h3>
          <p className="av-muted">{w("timeline.unplaced_hint")}</p>
          <ul className="av-understand-list">
            {[...unplaced]
              .sort((a, b) => a.page_no - b.page_no)
              .map((e) => (
                <li key={e.fact_id} className="av-story-unplaced__row">
                  <button type="button" className="av-story-unplaced__open" data-facts={e.fact_id} aria-label={entryName(e)} onClick={() => onOpen([e], e.fact_id)}>
                    {e.summary || kindWord(e.kind)}
                  </button>
                  <PageRef page={e.page_no} />
                </li>
              ))}
          </ul>
        </section>
      )}
      <div className="av-understand-foot">
        <div className="av-understand-keys">
          <p className="av-story-shown">{w("timeline.shown", { shown: NUMBER.format(shown), total: NUMBER.format(total) })}</p>
          <ul className="av-understand-legend" aria-label={w("timeline.legend_dots")}>
            <li><span className="av-understand-key av-understand-key--checked" aria-hidden="true" />{t("ui.status.checked")}</li>
            <li><span className="av-understand-key av-understand-key--please_check" aria-hidden="true" />{t("ui.status.please_check")}</li>
            <li><span className="av-understand-key av-understand-key--look" aria-hidden="true" />{t("ui.status.look")}</li>
            <li><QChip n={2} />{w("timeline.legend_question")}</li>
          </ul>
          {data.sources && data.sources.length > 0 && (
            <ul className="av-understand-legend" aria-label={w("timeline.legend")}>
              {data.sources.map((s) => (
                <li key={s.key}>
                  <span className="av-understand-swatch" style={{ background: s.colour ?? undefined }} aria-hidden="true" />
                  {s.label}
                </li>
              ))}
            </ul>
          )}
        </div>
        {found > 0 && (
          <LinkButton to={screenPath("things_to_look_at", { caseId }) ?? `/cases/${caseId}/things-to-look-at`} arrow>
            {w("timeline.look", { count: found })}
          </LinkButton>
        )}
      </div>
    </div>
  );
}

