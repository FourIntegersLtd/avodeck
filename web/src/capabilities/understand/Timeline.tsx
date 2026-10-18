// S11 The timeline. Lanes, their order and their words come from the API (the case's pack); this screen only
// draws them. Filters (dates, status, question) are sent to the server, which does the filtering.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { LoadError, Loading, StatusBadge } from "../../foundation/ui";
import { casePath, kindWord, longDate, pageLabel, statusOf } from "./shared";
import { w } from "./words";

type Event = Schemas["Event"];

function EventRow({ caseId, event }: { caseId: string; event: Event }) {
  const linked = event.question_ids.length;
  return (
    <li className="av-understand-event">
      <Link to={`/cases/${caseId}/consultation/${event.fact_id}`}>
        {kindWord(event.kind)} · {longDate(event.event_date)}
      </Link>
      <span className="av-muted">{pageLabel(event.page_no)}</span>
      <StatusBadge status={statusOf(event.status)} />
      {event.has_finding && <StatusBadge status="look" />}
      <span className="av-muted">{linked ? w("timeline.linked", { count: linked }) : w("timeline.not_linked")}</span>
    </li>
  );
}

export function TimelineView({ caseId, data }: { caseId: string; data: Schemas["Timeline"] }) {
  const empty = data.lanes.every((l) => l.events.length === 0) && data.unplaced.length === 0;
  return (
    <div className="av-stack">
      {empty && <p className="av-muted">{w("timeline.nothing")}</p>}
      {data.lanes.map((lane) => (
        <section key={lane.key} aria-labelledby={`lane-${lane.key}`} className="av-card av-understand-lane">
          <h3 id={`lane-${lane.key}`}>{lane.label}</h3>
          {lane.events.length === 0 ? (
            <p className="av-muted">{w("timeline.empty_lane")}</p>
          ) : (
            <ul className="av-understand-list">
              {lane.events.map((e) => (
                <EventRow key={e.fact_id} caseId={caseId} event={e} />
              ))}
            </ul>
          )}
        </section>
      ))}
      {data.unplaced.length > 0 && (
        <section aria-labelledby="timeline-unplaced" className="av-card av-understand-lane">
          <h3 id="timeline-unplaced">{w("timeline.unplaced")}</h3>
          <p className="av-muted">{w("timeline.unplaced_hint")}</p>
          <ul className="av-understand-list">
            {data.unplaced.map((e) => (
              <EventRow key={e.fact_id} caseId={caseId} event={e} />
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
  const asked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId]);
  const found = useResource(async () => {
    const query: Record<string, string> = {};
    if (status) query.status = status;
    if (questionId) query.question_id = questionId;
    if (from) query.from = from;
    if (to) query.to = to;
    return unwrap(api.GET("/v1/cases/{case_id}/timeline", { params: { path: { case_id: caseId }, query: query as never } }));
  }, [caseId, status, questionId, from, to]);

  return (
    <div className="av-page av-stack">
      <div>
        <h2>{w("timeline.title")}</h2>
        <p className="av-muted">{w("timeline.intro")}</p>
      </div>
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
      {found.error ? <LoadError onRetry={found.reload} /> : found.data ? <TimelineView caseId={caseId} data={found.data} /> : <Loading />}
    </div>
  );
}
