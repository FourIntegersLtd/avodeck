// S11 The timeline. Lanes, their order and their words come from the API (the case's pack); this screen only
// draws them. Filters (dates, status, question) are sent to the server, which does the filtering.
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { ChoicePills, LinkButton, LoadError, Loading, PageRef, QChip, Segmented, StatusBadge, ToggleSwitch } from "../../foundation/ui";
import { LaneIcon } from "./laneIcons";
import { axisIso, casePath, entryHref, GuidanceChanged, kindWord, longDate, statusOf } from "./shared";
import { TimelineActivity } from "./TimelineActivity";
import { TimelineChronology } from "./TimelineChronology";
import { labelledDotIds, thinMonthMarks } from "./timelineLayout";
import { periodDefaultDates, type TimelineWindowPreset } from "./timelineTimeWindow";
import { w } from "./words";

type Event = Schemas["Event"];
type Source = Schemas["TimelineSource"];
type Zoom = "whole" | "month";
type TimelineViewMode = "chronology" | "activity" | "map";

const DAY = 86_400_000;
const at = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
};

export { axisIso } from "./shared";

type Lane = Schemas["Timeline"]["lanes"][number];

/** Entries with no date for the axis are listed apart, not stacked at the left of the track. */
export function splitForTrack(data: { lanes: Lane[]; unplaced: Event[] }) {
  const unplaced = [...data.unplaced];
  const lanes = data.lanes.map((lane) => {
    const onTrack: Event[] = [];
    for (const e of lane.events) {
      if (at(axisIso(e)) !== null) onTrack.push(e);
      else unplaced.push(e);
    }
    return { ...lane, events: onTrack };
  });
  return { lanes, unplaced };
}

/** The first of each month from the earliest entry to the latest, and the span in days they cover. */
function monthsOf(events: Event[]) {
  const days = events.map((e) => at(axisIso(e))).filter((d): d is number => d !== null);
  if (days.length === 0) return null;
  const first = new Date(Math.min(...days));
  const last = new Date(Math.max(...days));
  const start = Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1);
  const end = Date.UTC(last.getUTCFullYear(), last.getUTCMonth() + 1, 1);
  const marks: { at: number; label: string }[] = [];
  for (let d = new Date(start), i = 0; d.getTime() < end; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)), i++) {
    const long = i === 0 || d.getUTCMonth() === 0;
    marks.push({ at: d.getTime(), label: new Intl.DateTimeFormat("en-GB", { month: "short", ...(long ? { year: "numeric" } : {}), timeZone: "UTC" }).format(d) });
  }
  return { start, span: Math.max(end - start, DAY), marks };
}

const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const shortDay = (iso: string) => SHORT_DAY.format(new Date(`${iso}T00:00:00Z`));
function EventRow({ caseId, event, left, labelled, numbers }: { caseId: string; event: Event; left?: number; labelled?: boolean; numbers?: Record<string, number> }) {
  const linked = event.question_ids.length;
  const chips = event.question_ids.map((id) => numbers?.[id]).filter((n): n is number => n !== undefined);
  const style = left === undefined ? undefined : ({ left: `${left}%` } as React.CSSProperties);
  const state = event.has_finding ? "look" : statusOf(event.status) === "checked" ? "checked" : "please_check";
  const chipsNode =
    chips.length > 0 ? (
      <span className="av-understand-chips">
        {chips.map((n) => (
          <QChip key={n} n={n} />
        ))}
      </span>
    ) : null;
  const dot = left !== undefined;
  // A dot's details show only on hover or focus, so they are drawn then: a long record has 1,500 dots.
  const [open, setOpen] = useState(false);
  const show = () => setOpen(true);
  return (
    <li
      className={`av-understand-event${dot ? " av-understand-event--dot" : ""}${dot && open ? " av-understand-event--open" : ""}`}
      style={style}
      data-state={dot ? state : undefined}
      onMouseEnter={dot ? show : undefined}
      onFocus={dot ? show : undefined}
    >
      {left !== undefined && chipsNode}
      <Link to={entryHref(caseId, event)} aria-label={axisIso(event) ? undefined : w("timeline.open_in_read")}>
        <span className={left === undefined ? undefined : "av-visually-hidden"}>{kindWord(event.kind)} · {longDate(event.event_date)}</span>
      </Link>
      {left !== undefined && labelled && axisIso(event) && (
        <span className="av-understand-dotlabel" aria-hidden="true">
          <strong>{shortDay(axisIso(event)!)}</strong>
        </span>
      )}
      {(!dot || open) && <span className="av-understand-tip">
        {event.summary && <span>{event.summary}</span>}
        <PageRef page={event.page_no} to={entryHref(caseId, event)} />
        <StatusBadge status={statusOf(event.status)} />
        {event.has_finding && <StatusBadge status="look" />}
        <span className="av-muted">{linked ? w("timeline.linked", { count: linked }) : w("timeline.not_linked")}</span>
      </span>}
      {left === undefined && chipsNode}
    </li>
  );
}

type TimelineData = Omit<Schemas["Timeline"], "guidance_changed" | "sources" | "counts"> & { guidance_changed?: string[]; sources?: Source[]; counts?: Schemas["TimelineCounts"] | null; range?: { first: string; last: string } | null };

const monthYear = (iso: string, year: boolean) => new Intl.DateTimeFormat("en-GB", { month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));

/** "Feb to Oct 2024", or with both years when the record crosses a year. */
function rangeWords(r: { first: string; last: string }): string {
  const same = r.first.slice(0, 4) === r.last.slice(0, 4);
  return w("timeline.range", { from: monthYear(r.first, !same), to: monthYear(r.last, true) });
}

export function TimelineView({ caseId, data, numbers, zoom = "whole" }: { caseId: string; data: TimelineData; numbers?: Record<string, number>; zoom?: Zoom }) {
  const { lanes: allLanes, unplaced: rawUnplaced } = splitForTrack(data);
  const lanes = allLanes.filter((l) => l.events.length > 0);
  const unplaced = [...rawUnplaced].sort((a, b) => a.page_no - b.page_no);
  const onTrack = lanes.some((l) => l.events.length > 0);
  const undated = data.counts?.undated ?? 0;
  const showUndatedBanner = undated > 0 && !onTrack && unplaced.length > 0;
  const empty = lanes.every((l) => l.events.length === 0) && unplaced.length === 0;
  const range = monthsOf(lanes.flatMap((l) => l.events));
  const monthMarks = range ? thinMonthMarks(range.marks, zoom) : [];
  const trackMonths = range?.marks.length ?? 1;
  // Keep the first and last dots clear of the edges of the track.
  const place = (e: Event) => {
    const d = at(axisIso(e));
    return range && d !== null ? 3 + ((d - range.start) / range.span) * 94 : 0;
  };
  const found = data.counts?.things_to_look ?? 0;
  return (
    <div className="av-stack">
      <GuidanceChanged codes={data.guidance_changed} />
      {showUndatedBanner && (
        <div className="av-card av-understand-undated-banner" data-testid="timeline-undated-banner">
          <p>{w("timeline.undated_banner", { count: undated })}</p>
          <LinkButton to={screenPath("reader", { caseId }) ?? `/cases/${caseId}`} arrow>
            {w("timeline.undated_banner_link")}
          </LinkButton>
        </div>
      )}
      {data.range && <p className="av-understand-range"><span className="av-understand-count">{rangeWords(data.range)}</span></p>}
      {empty && <p className="av-muted">{w("timeline.nothing")}</p>}
      {!empty && <p className="av-muted">{w("timeline.scroll_hint")}</p>}
      <div className="av-understand-board" data-testid="timeline-board" data-zoom={zoom}>
        <div
          className="av-understand-canvas"
          style={{ "--track-months": trackMonths, "--months": trackMonths } as React.CSSProperties}
          data-track-months={trackMonths}
        >
          <div className="av-understand-row av-understand-row--months">
            <div className="av-understand-row__head" />
            {range && (
              <ul className="av-understand-months" aria-label={w("timeline.months")}>
                {monthMarks.map((m) => (
                  <li key={m.at} style={{ left: `${((m.at - range.start) / range.span) * 100}%` }}>
                    {m.label}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {lanes.map((lane) => {
            const placed = lane.events.map((e) => ({ id: e.fact_id, left: place(e) }));
            const labelIds = labelledDotIds(placed);
            const crowded = lane.events.length > 4 || labelIds.size < lane.events.length;
            const showList = lane.events.length > 4 || crowded;
            const dotsOnly = labelIds.size === 0;
            return (
            <section key={lane.key} aria-labelledby={`lane-${lane.key}`} className="av-understand-row av-understand-lane">
              <div className="av-understand-row__head av-understand-lane__head">
                <span className="av-understand-lane__icon" data-testid="lane-icon" aria-hidden="true" style={{ background: lane.colour ?? "var(--ink-soft)" }}>
                  <LaneIcon laneKey={lane.key} label={lane.label} />
                </span>
                <span>
                  <h3 id={`lane-${lane.key}`}>{lane.label}</h3>
                  <span className="av-understand-lane__count">{w("timeline.lane_count", { count: lane.events.length })}</span>
                </span>
              </div>
              {lane.events.length === 0 ? (
                <p className="av-muted av-understand-lane__empty">{w("timeline.empty_lane")}</p>
              ) : (
                <>
                  {crowded && <p className="av-muted av-understand-lane__dense">{w("timeline.dense_lane")}</p>}
                  <ul className={`av-understand-list av-understand-track${dotsOnly ? " av-understand-track--dots-only" : ""}`}>
                    {lane.events.map((e) => (
                      <EventRow key={e.fact_id} caseId={caseId} event={e} left={place(e)} labelled={labelIds.has(e.fact_id)} numbers={numbers} />
                    ))}
                  </ul>
                  {showList && (
                    <details className="av-understand-lane__list" open>
                      <summary>{w("timeline.lane_list", { count: lane.events.length })}</summary>
                      <ul className="av-understand-list">
                        {lane.events.map((e) => (
                          <EventRow key={`list-${e.fact_id}`} caseId={caseId} event={e} numbers={numbers} />
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              )}
            </section>
            );
          })}
        </div>
      </div>
      {unplaced.length > 0 && (
        <section aria-labelledby="timeline-unplaced" className="av-card av-understand-lane av-understand-unplaced">
          <h3 id="timeline-unplaced">{w("timeline.unplaced")}</h3>
          <p className="av-muted">{w("timeline.unplaced_hint")}</p>
          <ul className="av-understand-list">
            {unplaced.map((e) => (
              <EventRow key={e.fact_id} caseId={caseId} event={e} numbers={numbers} />
            ))}
          </ul>
        </section>
      )}
      <div className="av-understand-foot">
        <div className="av-understand-keys">
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

export function Timeline() {
  const { caseId = "" } = useParams();
  const [status, setStatus] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [windowPreset, setWindowPreset] = useState<TimelineWindowPreset>("period");
  const [periodSeeded, setPeriodSeeded] = useState(false);
  const [seenOn, setSeenOn] = useState("");
  const [dayOn, setDayOn] = useState(false);
  const [adjust, setAdjust] = useState(false);
  const lastCounts = useRef<Schemas["TimelineCounts"] | null>(null);
  const [zoom, setZoom] = useState<Zoom>("whole");
  const [viewMode, setViewMode] = useState<TimelineViewMode>("chronology");
  const asked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId]);
  const caseRow = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", casePath(caseId))), [caseId]);
  const found = useResource(async () => {
    const query: Record<string, string> = {};
    if (status === "verified" || status === "suggested") query.status = status;
    if (status === "not_linked" || status === "things_to_look") query.only = status;
    if (/^\d{4}-\d{2}-\d{2}$/.test(seenOn)) query.as_seen_on = seenOn;
    if (questionId) query.question_id = questionId;
    if (from) query.from = from;
    if (to) query.to = to;
    return unwrap(api.GET("/v1/cases/{case_id}/timeline", { params: { path: { case_id: caseId }, query: query as never } }));
  }, [caseId, status, questionId, from, to, seenOn]);

  useEffect(() => {
    if (windowPreset !== "period" || periodSeeded) return;
    const dates = periodDefaultDates(caseRow.data, found.data?.range ?? null);
    if (dates) {
      setFrom(dates.from);
      setTo(dates.to);
      setPeriodSeeded(true);
    }
  }, [windowPreset, periodSeeded, caseRow.data, found.data?.range]);

  const applyWindowPreset = (preset: TimelineWindowPreset) => {
    setWindowPreset(preset);
    if (preset === "whole") {
      setFrom("");
      setTo("");
      return;
    }
    if (preset === "period") {
      setPeriodSeeded(false);
      const dates = periodDefaultDates(caseRow.data, found.data?.range ?? null);
      if (dates) {
        setFrom(dates.from);
        setTo(dates.to);
        setPeriodSeeded(true);
      }
      return;
    }
    setPeriodSeeded(true);
  };

  const showingLabel =
    windowPreset === "whole" || (!from && !to)
      ? w("timeline.showing", { range: w("timeline.showing.whole") })
      : w("timeline.showing", { range: `${longDate(from || found.data?.range?.first)} – ${longDate(to || found.data?.range?.last)}` });

  const c = found.data?.counts ?? lastCounts.current;
  if (found.data?.counts) lastCounts.current = found.data.counts;
  const numbers = Object.fromEntries((asked.data?.questions ?? []).map((q) => [q.id, q.number]));
  const seenTitle = found.data?.as_seen_title ?? w("consultation.on_label");
  const switchDay = (on: boolean) => {
    setDayOn(on);
    if (on) setAdjust(true);
    else setSeenOn("");
  };
  return (
    <div className="av-page av-page--wide av-stack">
      <div className="av-understand-top">
        <div>
          <h2>{w("timeline.title")}</h2>
          <p className="av-muted">{w("timeline.intro")}</p>
          <p className="av-muted">{w("timeline.blind_read_hint")}</p>
        </div>
        <div className="av-understand-top__tools">
          <ToggleSwitch label={w("timeline.as_seen_toggle", { title: seenTitle })} checked={dayOn || seenOn !== ""} onChange={switchDay} />
        </div>
      </div>
      <div className="av-understand-time-window" aria-label={w("timeline.window")}>
        <Segmented
          label={w("timeline.window")}
          hideLabel
          value={windowPreset}
          onChange={(v) => applyWindowPreset(v as TimelineWindowPreset)}
          options={[
            { value: "whole", label: w("timeline.window.whole") },
            { value: "period", label: w("timeline.window.period") },
            { value: "custom", label: w("timeline.window.custom") },
          ]}
        />
        {(windowPreset === "custom" || windowPreset === "period") && (
          <form className="av-understand-time-window__dates" onSubmit={(e) => e.preventDefault()}>
            <div className="av-field">
              <label className="av-field__label" htmlFor="tl-from">{w("timeline.filter.from")}</label>
              <input
                id="tl-from"
                className="av-input"
                type="date"
                value={from}
                onChange={(e) => {
                  setWindowPreset("custom");
                  setFrom(e.target.value);
                }}
              />
            </div>
            <div className="av-field">
              <label className="av-field__label" htmlFor="tl-to">{w("timeline.filter.to")}</label>
              <input
                id="tl-to"
                className="av-input"
                type="date"
                value={to}
                onChange={(e) => {
                  setWindowPreset("custom");
                  setTo(e.target.value);
                }}
              />
            </div>
            {(from || to) && (
              <button type="button" className="av-understand-time-window__clear" onClick={() => applyWindowPreset("whole")}>
                {w("timeline.clear_dates")}
              </button>
            )}
          </form>
        )}
        <p className="av-muted av-understand-time-window__showing">{showingLabel}</p>
      </div>
      <Segmented
        label={w("timeline.view")}
        value={viewMode}
        onChange={(v) => setViewMode(v as TimelineViewMode)}
        options={[
          { value: "chronology", label: w("timeline.view.chronology") },
          { value: "activity", label: w("timeline.view.activity") },
          { value: "map", label: w("timeline.view.map") },
        ]}
      />
      {viewMode === "map" && (
        <Segmented
          label={w("timeline.zoom")}
          hideLabel
          value={zoom}
          onChange={(v) => setZoom(v as Zoom)}
          options={[
            { value: "whole", label: w("timeline.zoom.whole") },
            { value: "month", label: w("timeline.zoom.month") },
          ]}
        />
      )}
      <ChoicePills
        label={w("timeline.filter.status")}
        hideLabel
        value={status}
        onChange={setStatus}
        options={[
          { value: "", label: w("timeline.filter.status.all") },
          ...(c
            ? [
                { value: "verified", label: w("timeline.count", { label: t("ui.status.checked"), n: c.checked }) },
                { value: "suggested", label: w("timeline.count", { label: t("ui.status.please_check"), n: c.please_check }) },
                { value: "things_to_look", label: w("timeline.count", { label: t("ui.status.look"), n: c.things_to_look }) },
                { value: "not_linked", label: w("timeline.count", { label: w("timeline.not_linked"), n: c.not_linked }) },
              ]
            : []),
        ]}
      />
      <details className="av-understand-adjust" open={adjust} onToggle={(e) => setAdjust(e.currentTarget.open)}>
        <summary>{w("timeline.adjust")}</summary>
        <form className="av-card av-understand-filters" aria-label={w("timeline.filters")} onSubmit={(e) => e.preventDefault()}>
          <div className="av-field">
            <label className="av-field__label" htmlFor="tl-question">{w("timeline.filter.question")}</label>
            <select id="tl-question" className="av-input" value={questionId} onChange={(e) => setQuestionId(e.target.value)}>
              <option value="">{w("timeline.filter.question.all")}</option>
              {(asked.data?.questions ?? []).map((q) => (
                <option key={q.id} value={q.id}>
                  {w("timeline.filter.question.option", { n: q.number })}
                </option>
              ))}
            </select>
          </div>
          <div className="av-field">
            <label className="av-field__label" htmlFor="tl-seen">{w("timeline.as_seen_on", { title: seenTitle })}</label>
            <input id="tl-seen" className="av-input" type="date" value={seenOn} onChange={(e) => setSeenOn(e.target.value)} />
          </div>
        </form>
      </details>
      {found.error ? (
        <LoadError onRetry={found.reload} />
      ) : found.data ? (
        <div className="av-stack">
          {(() => {
            const { lanes, unplaced } = splitForTrack(found.data);
            const onTrack = lanes.some((l) => l.events.length > 0);
            const undated = found.data.counts?.undated ?? 0;
            const showUndatedBanner = undated > 0 && !onTrack && unplaced.length > 0;
            return showUndatedBanner ? (
              <div className="av-card av-understand-undated-banner" data-testid="timeline-undated-banner">
                <p>{w("timeline.undated_banner", { count: undated })}</p>
                <LinkButton to={screenPath("reader", { caseId }) ?? `/cases/${caseId}`} arrow>
                  {w("timeline.undated_banner_link")}
                </LinkButton>
              </div>
            ) : null;
          })()}
          {viewMode === "chronology" ? (
            <>
              <p className="av-muted">{w("timeline.chronology_hint")}</p>
              <TimelineChronology caseId={caseId} data={found.data} numbers={numbers} />
            </>
          ) : viewMode === "activity" ? (
            <TimelineActivity
              caseId={caseId}
              data={found.data}
              numbers={numbers}
              windowFrom={from || found.data.range?.first || ""}
              windowTo={to || found.data.range?.last || ""}
              onNarrowWindow={(narrowFrom, narrowTo) => {
                setWindowPreset("custom");
                setFrom(narrowFrom);
                setTo(narrowTo);
              }}
              onResetWindow={() => applyWindowPreset("period")}
              onOpenChronology={() => setViewMode("chronology")}
            />
          ) : (
            <TimelineView caseId={caseId} data={found.data} numbers={numbers} zoom={zoom} />
          )}
        </div>
      ) : (
        <Loading />
      )}
    </div>
  );
}
