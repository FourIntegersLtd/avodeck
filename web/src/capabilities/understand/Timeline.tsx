// S11 The timeline. Lanes, their order and their words come from the API (the case's pack); this screen only
// draws them. Filters (dates, status, question) are sent to the server, which does the filtering.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { LoadError, Loading, QChip, Segmented, StatusBadge } from "../../foundation/ui";
import { casePath, GuidanceChanged, kindWord, longDate, pageLabel, statusOf } from "./shared";
import { w } from "./words";

type Event = Schemas["Event"];
type Source = Schemas["TimelineSource"];
type Zoom = "whole" | "month";

const DAY = 86_400_000;
const at = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
};

/** The first of each month from the earliest entry to the latest, and the span in days they cover. */
function monthsOf(events: Event[]) {
  const days = events.map((e) => at(e.event_date)).filter((d): d is number => d !== null);
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

function EventRow({ caseId, event, left, colour, numbers }: { caseId: string; event: Event; left?: number; colour?: string; numbers?: Record<string, number> }) {
  const linked = event.question_ids.length;
  const chips = event.question_ids.map((id) => numbers?.[id]).filter((n): n is number => n !== undefined);
  const style = left === undefined ? undefined : ({ left: `${left}%`, "--lane-colour": colour ?? "var(--ink-muted)" } as React.CSSProperties);
  return (
    <li className={`av-understand-event${left === undefined ? "" : " av-understand-event--dot"}`} style={style}>
      <Link to={`/cases/${caseId}/consultation/${event.fact_id}`}>
        {kindWord(event.kind)} · {longDate(event.event_date)}
      </Link>
      <span className="av-understand-tip">
        {event.summary && <span>{event.summary}</span>}
        <span className="av-muted">{pageLabel(event.page_no)}</span>
        <StatusBadge status={statusOf(event.status)} />
        {event.has_finding && <StatusBadge status="look" />}
        <span className="av-muted">{linked ? w("timeline.linked", { count: linked }) : w("timeline.not_linked")}</span>
      </span>
      {chips.length > 0 && (
        <span className="av-understand-chips">
          {chips.map((n) => (
            <QChip key={n} n={n} />
          ))}
        </span>
      )}
    </li>
  );
}

type TimelineData = Omit<Schemas["Timeline"], "guidance_changed" | "sources" | "counts"> & { guidance_changed?: string[]; sources?: Source[]; counts?: Schemas["TimelineCounts"] | null };

function Counts({ counts }: { counts: Schemas["TimelineCounts"] }) {
  const chip = (label: string, n: number) => <span className="av-understand-count">{w("timeline.count", { label, n })}</span>;
  return (
    <div role="group" aria-label={w("timeline.counts")} className="av-understand-counts">
      {chip(t("ui.status.checked"), counts.checked)}
      {chip(t("ui.status.please_check"), counts.please_check)}
      {chip(t("ui.status.look"), counts.things_to_look)}
      {chip(w("timeline.not_linked"), counts.not_linked)}
    </div>
  );
}

export function TimelineView({ caseId, data, numbers, zoom = "whole" }: { caseId: string; data: TimelineData; numbers?: Record<string, number>; zoom?: Zoom }) {
  const empty = data.lanes.every((l) => l.events.length === 0) && data.unplaced.length === 0;
  const colours = new Map((data.sources ?? []).map((s) => [s.key, s.colour]));
  const range = monthsOf(data.lanes.flatMap((l) => l.events));
  const place = (e: Event) => {
    const d = at(e.event_date);
    return range && d !== null ? ((d - range.start) / range.span) * 100 : 0;
  };
  return (
    <div className="av-stack">
      <GuidanceChanged codes={data.guidance_changed} />
      {data.counts && <Counts counts={data.counts} />}
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
      {empty && <p className="av-muted">{w("timeline.nothing")}</p>}
      <div className="av-understand-board" data-testid="timeline-board" data-zoom={zoom}>
        <div className="av-understand-canvas" style={{ "--months": range ? range.marks.length : 1 } as React.CSSProperties}>
          {range && (
            <ul className="av-understand-months" aria-label={w("timeline.months")}>
              {range.marks.map((m) => (
                <li key={m.at} style={{ left: `${((m.at - range.start) / range.span) * 100}%` }}>
                  {m.label}
                </li>
              ))}
            </ul>
          )}
          {data.lanes.map((lane) => (
            <section key={lane.key} aria-labelledby={`lane-${lane.key}`} className="av-card av-understand-lane">
              <div className="av-understand-lane__head">
                <h3 id={`lane-${lane.key}`}>{lane.label}</h3>
                <span className="av-muted">{w("timeline.lane_count", { count: lane.events.length })}</span>
              </div>
              {lane.events.length === 0 ? (
                <p className="av-muted">{w("timeline.empty_lane")}</p>
              ) : (
                <ul className="av-understand-list av-understand-track">
                  {lane.events.map((e) => (
                    <EventRow key={e.fact_id} caseId={caseId} event={e} left={place(e)} colour={(e.source && colours.get(e.source)) || undefined} numbers={numbers} />
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
      </div>
      {data.unplaced.length > 0 && (
        <section aria-labelledby="timeline-unplaced" className="av-card av-understand-lane">
          <h3 id="timeline-unplaced">{w("timeline.unplaced")}</h3>
          <p className="av-muted">{w("timeline.unplaced_hint")}</p>
          <ul className="av-understand-list">
            {data.unplaced.map((e) => (
              <EventRow key={e.fact_id} caseId={caseId} event={e} numbers={numbers} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function Timeline() {
  const { caseId = "" } = useParams();
  const [status, setStatus] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [zoom, setZoom] = useState<Zoom>("whole");
  const asked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId]);
  const found = useResource(async () => {
    const query: Record<string, string> = {};
    if (status) query.status = status;
    if (questionId) query.question_id = questionId;
    if (from) query.from = from;
    if (to) query.to = to;
    return unwrap(api.GET("/v1/cases/{case_id}/timeline", { params: { path: { case_id: caseId }, query: query as never } }));
  }, [caseId, status, questionId, from, to]);

  const numbers = Object.fromEntries((asked.data?.questions ?? []).map((q) => [q.id, q.number]));
  return (
    <div className="av-page av-stack">
      <div>
        <h2>{w("timeline.title")}</h2>
        <p className="av-muted">{w("timeline.intro")}</p>
      </div>
      <Segmented
        label={w("timeline.zoom")}
        value={zoom}
        onChange={(v) => setZoom(v as Zoom)}
        options={[
          { value: "whole", label: w("timeline.zoom.whole") },
          { value: "month", label: w("timeline.zoom.month") },
        ]}
      />
      <form className="av-card av-understand-filters" aria-label={w("timeline.filters")} onSubmit={(e) => e.preventDefault()}>
        <div className="av-field">
          <label className="av-field__label" htmlFor="tl-from">{w("timeline.filter.from")}</label>
          <input id="tl-from" className="av-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="av-field">
          <label className="av-field__label" htmlFor="tl-to">{w("timeline.filter.to")}</label>
          <input id="tl-to" className="av-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="av-field">
          <label className="av-field__label" htmlFor="tl-status">{w("timeline.filter.status")}</label>
          <select id="tl-status" className="av-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{w("timeline.filter.status.all")}</option>
            <option value="verified">{t("ui.status.checked")}</option>
            <option value="suggested">{t("ui.status.please_check")}</option>
          </select>
        </div>
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
      </form>
      {found.error ? <LoadError onRetry={found.reload} /> : found.data ? <TimelineView caseId={caseId} data={found.data} numbers={numbers} zoom={zoom} /> : <Loading />}
    </div>
  );
}
