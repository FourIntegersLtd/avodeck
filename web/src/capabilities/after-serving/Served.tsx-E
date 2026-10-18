// S19 After serving (docs/design/screens/S19-Served.dc.html): what was served, locked and read-only, the dates, and the
// ways on to the questions, the new material, a correction and the court orders.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { keyName, longDate, useLocale } from "./shared";
import { w } from "./words";

export function ServedScreen() {
  const { caseId = "" } = useParams();
  const locale = useLocale();
  const path = { params: { path: { case_id: caseId } } };
  const served = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/served", path)), [caseId]);
  const deadlines = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deadlines", path)), [caseId]);
  const rounds = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-question-rounds", path)), [caseId]);
  const [open, setOpen] = useState(false);

  if (served.error || deadlines.error || rounds.error) {
    return <div className="av-page"><LoadError onRetry={() => { served.reload(); deadlines.reload(); rounds.reload(); }} /></div>;
  }
  if (!served.data || !deadlines.data || !rounds.data) return <div className="av-page"><Loading /></div>;
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
  const due = open_rounds
    .map((r) => deadlines.data!.deadlines.find((d) => d.id === r.deadline_id)?.due_on)
    .filter((d): d is string => !!d)
    .sort()[0];
  const dates = [...deadlines.data.deadlines].sort((a, b) => a.due_on.localeCompare(b.due_on));

  return (
    <div className="av-page av-stack">
      <h2>{w("served.title")}</h2>
      <p>{w("served.intro", { date: version.served_on ? longDate(version.served_on, locale) : "" })}</p>

      <section className="av-card av-stack" aria-labelledby="served-locked">
        <div className="av-after-item--spread av-row">
          <h3 id="served-locked">{w("served.locked")}</h3>
          <Button variant="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? w("served.hide") : w("served.view")}</Button>
        </div>
        <p className="av-muted">{version.display_name}</p>
        {open && (
          <ul className="av-after-list">
            {served.data.paragraphs.map((p) => (
              <li key={p.id} className="av-after-item">
                <strong className="av-after-mono">{w("served.paragraph", { n: p.para_no })}</strong>
                <p className="av-after-words">{p.text}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="av-card av-stack" aria-labelledby="served-dates">
        <h3 id="served-dates">{w("served.dates")}</h3>
        {dates.length === 0 && <p>{w("served.no_dates")}</p>}
        <ul className="av-after-list">
          {dates.map((d) => (
            <li key={d.id} className="av-after-item av-after-item--spread">
              <strong>{keyName("kind", d.kind)}</strong>
              <span className="av-after-mono">{w("due", { date: longDate(d.due_on, locale) })}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="av-card av-stack" aria-labelledby="served-questions">
        <h3 id="served-questions">{w("served.questions")}</h3>
        <p>{due ? w("served.questions_body", { date: longDate(due, locale) }) : w("served.questions_none")}</p>
        <LinkButton to={`/cases/${caseId}/written-questions`}>{w("served.questions_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-new">
        <h3 id="served-new">{w("served.new")}</h3>
        <p>{w("served.new_body")}</p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/new-material`}>{w("served.new_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-changes">
        <h3 id="served-changes">{w("served.changes")}</h3>
        <p>{w("served.changes_body")}</p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/correct-report`}>{w("served.changes_action")}</LinkButton>
      </section>
      <section className="av-card av-stack" aria-labelledby="served-orders">
        <h3 id="served-orders">{w("served.orders")}</h3>
        <p>{w("served.orders_body")}</p>
        <LinkButton variant="secondary" to={`/cases/${caseId}/court-orders`}>{w("served.orders_action")}</LinkButton>
      </section>
    </div>
  );
}
