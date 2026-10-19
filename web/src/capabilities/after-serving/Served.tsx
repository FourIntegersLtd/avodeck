// S19 After serving (docs/design/screens/S19-Served.dc.html): what was served, locked and read-only, the dates, and the
// ways on to the questions, the new material, a correction and the court orders.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { Button, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { capitalise, keyName, longDate, dayMonth, shortDate, useLocale } from "./shared";
import { w } from "./words";

export function ServedScreen() {
  const { caseId = "" } = useParams();
  const locale = useLocale();
  const path = { params: { path: { case_id: caseId } } };
  const served = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/served", path)), [caseId]);
  const deadlines = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deadlines", path)), [caseId]);
  const rounds = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-question-rounds", path)), [caseId]);
  const questions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-questions", path)), [caseId]);
  const fresh = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/new-since-served", path)), [caseId]);
  const changes = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report-changes", path)), [caseId]);
  const hearings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/hearings", path)), [caseId]);
  const directions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/court-directions", path)), [caseId]);
  const steps = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/after-serving/steps", path)), [caseId]);
  const { pack } = useCaseFrame();
  const [open, setOpen] = useState(false);

  const all = [served, deadlines, rounds, questions, fresh, changes, hearings, directions, steps];
  if (all.some((r) => r.error)) {
    return <div className="av-page"><LoadError onRetry={() => all.forEach((r) => r.reload())} /></div>;
  }
  if (!served.data || !deadlines.data || !rounds.data || !questions.data || !fresh.data || !changes.data || !hearings.data || !directions.data || !steps.data) {
    return <div className="av-page"><Loading /></div>;
  }
  const version = served.data.version;
  if (!version) {
    return (
      <div className="av-page av-stack">
        <h2>{w("served.title")}</h2>
        <Notice><p>{w("served.not_served")}</p></Notice>
      </div>
    );
  }
  const open_rounds = rounds.data.rounds.filter((r) => r.status !== "answered");
  const firstRound = open_rounds[0];
  const roundDeadline = firstRound ? deadlines.data.deadlines.find((d) => d.id === firstRound.deadline_id) : undefined;
  const due = open_rounds
    .map((r) => deadlines.data!.deadlines.find((d) => d.id === r.deadline_id)?.due_on)
    .filter((d): d is string => !!d)
    .sort()[0];
  const dates = [...deadlines.data.deadlines].sort((a, b) => a.due_on.localeCompare(b.due_on));
  const upcoming = hearings.data.hearings.filter((h) => !h.replaced_by_hearing_id && h.status !== "adjourned").sort((a, b) => a.starts_on.localeCompare(b.starts_on));
  const nextHearing = upcoming[0];
  const count = questions.data.questions.length;
  const vocab = String((pack.vocabulary as Record<string, unknown>).written_questions ?? "");
  const pages = fresh.data.pages.length;
  const made = changes.data.changes.length;
  const orders = directions.data.directions.length;
  const days = (h: { starts_on: string; ends_on: string }) => Math.round((Date.parse(h.ends_on) - Date.parse(h.starts_on)) / 86400000) + 1;
  const left = roundDeadline?.days_left;

  return (
    <div className="av-page av-stack">
      <h2>{w("served.title")}</h2>
      <p>{w("served.intro", { date: version.served_on ? longDate(version.served_on, locale) : "" })}</p>

      <div className="av-after-pill av-row" role="group" aria-label={w("served.locked")}>
        <div className="av-after-pill__text">
          <strong>{w("served.locked")}</strong>
          <span className="av-muted">{version.display_name}</span>
        </div>
        <Button variant="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? w("served.hide") : w("served.view")}</Button>
      </div>
      {open && (
        <section className="av-card">
          <ul className="av-after-list">
            {served.data.paragraphs.map((p) => (
              <li key={p.id} className="av-after-item">
                <strong className="av-after-mono">{w("served.paragraph", { n: p.para_no })}</strong>
                <p className="av-after-words">{p.text}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="av-card av-stack" aria-labelledby="served-next">
        <h3 id="served-next">{w("served.next")}</h3>
        <ol className="av-after-timeline">
          {version.served_on && (
            <li className="av-after-timeline__item av-after-timeline__item--done">
              <strong>{w("served.step_served")}</strong>
              <span className="av-after-mono">{shortDate(version.served_on, locale)}</span>
            </li>
          )}
          {dates.map((d) => (
            <li key={d.id} className="av-after-timeline__item">
              <strong>{keyName("kind", d.kind)}</strong>
              <span className="av-after-mono">{w("due", { date: shortDate(d.due_on, locale) })}</span>
            </li>
          ))}
          {upcoming.map((h) => (
            <li key={h.id} className="av-after-timeline__item">
              <strong>{keyName("kind", h.type)}</strong>
              <span className="av-after-mono">{w("served.hearing_span", { date: longDate(h.starts_on, locale), count: days(h) })}</span>
            </li>
          ))}
          {steps.data.meetings.map((m) => {
            const to = screenPath("expert_meeting", { caseId });
            const name = w("served.step_meeting");
            return (
              <li key={m.id} className="av-after-timeline__item">
                {to ? <Link to={to}><strong>{name}</strong></Link> : <strong>{name}</strong>}
                <span className="av-after-mono">{w("served.step_meeting_on", { date: longDate(m.discussion_on, locale), status: w(`served.meeting_${m.status}`) })}</span>
              </li>
            );
          })}
          {nextHearing && (
            <li className="av-after-timeline__item">
              {(() => {
                const to = screenPath("trial_pack", { caseId });
                const name = capitalise(String((pack.vocabulary as Record<string, unknown>).court_bundle ?? "")) || w("served.step_bundle");
                return to ? <Link to={to}><strong>{name}</strong></Link> : <strong>{name}</strong>;
              })()}
              <span className="av-after-mono">
                {steps.data.pack ? (steps.data.pack.stale ? w("served.bundle_stale") : w("served.bundle_built", { date: longDate(steps.data.pack.built_on, locale) })) : w("served.bundle_none")}
              </span>
            </li>
          )}
        </ol>
      </section>

      <section className="av-card av-stack" aria-labelledby="served-questions">
        <h3 id="served-questions">{count > 0 ? `${count} ${vocab}` : w("served.questions")}</h3>
        <p>
          {count > 0 && firstRound && left != null
            ? w("served.questions_received", { date: dayMonth(firstRound.received_on, locale), count: left })
            : due ? w("served.questions_body", { date: longDate(due, locale) }) : w("served.questions_none")}
        </p>
        <LinkButton to={`/cases/${caseId}/written-questions`} arrow>{w("served.questions_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-new">
        <h3 id="served-new">{w("served.new")}</h3>
        <p>{pages > 0 ? w("served.new_pages", { count: pages }) : w("served.new_body")}</p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/new-material`} arrow>{w("served.new_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-changes">
        <h3 id="served-changes">{w("served.changes")}</h3>
        <p>{made > 0 ? w("served.changes_made", { count: made }) : w("served.changes_body")}</p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/correct-report`} arrow>{w("served.changes_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-orders">
        <h3 id="served-orders">{w("served.orders")}</h3>
        <p>
          {orders > 0
            ? `${w("served.orders_made", { count: orders })}${nextHearing ? ` ${w("served.orders_listed", { kind: keyName("kind", nextHearing.type), date: longDate(nextHearing.starts_on, locale) })}` : ""}`
            : w("served.orders_body")}
        </p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/court-orders`} arrow>{w("served.orders_action")}</LinkButton>
      </section>
    </div>
  );
}
