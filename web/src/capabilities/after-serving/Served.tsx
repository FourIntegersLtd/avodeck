// S19 After serving (docs/design/screens/S19-Served.dc.html): what was served, locked and read-only, the track of what
// comes next, and the ways on to the questions, the new material, a correction and the court orders.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { IconTile, LoadError, Loading, Notice, TextLink } from "../../foundation/ui";
import { ArrowIcon, GavelIcon, HelpIcon, LockIcon, NewDocIcon, PencilIcon, TickIcon } from "./icons";
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
  const upcoming = hearings.data.hearings.filter((h) => !h.replaced_by_hearing_id && h.status !== "adjourned").sort((a, b) => a.starts_on.localeCompare(b.starts_on));
  const nextHearing = upcoming[0];
  const count = questions.data.questions.length;
  const vocabulary = pack.vocabulary as Record<string, unknown>;
  const vocab = String(vocabulary.written_questions ?? "");
  const pages = fresh.data.pages.length;
  const made = changes.data.changes.length;
  const orders = directions.data.directions.length;
  const days = (h: { starts_on: string; ends_on: string }) => Math.round((Date.parse(h.ends_on) - Date.parse(h.starts_on)) / 86400000) + 1;
  const left = roundDeadline?.days_left;

  const meeting = steps.data.meetings[0];
  const bundle = steps.data.pack;
  const questionsDone = rounds.data.rounds.length > 0 && open_rounds.length === 0;
  const trial = upcoming.find((h) => h.type === "trial") ?? nextHearing;
  const track: Array<{ key: string; name: string; note: string; done: boolean; alert?: boolean; to?: string | null }> = [
    { key: "served", name: w("served.step_served"), note: version.served_on ? shortDate(version.served_on, locale) : "", done: true },
    {
      key: "questions", name: capitalise(vocab) || w("served.questions"), done: questionsDone, alert: !questionsDone && !!due,
      note: questionsDone ? w("served.status_answered") : due ? w("served.answer_by", { date: shortDate(due, locale) }) : w("served.questions_none"),
    },
    {
      key: "meeting", name: capitalise(String(vocabulary.expert_meeting ?? "")) || w("served.step_meeting"), done: !!meeting && meeting.status.startsWith("signed"),
      to: screenPath("expert_meeting", { caseId }),
      note: meeting ? w("served.step_meeting_on", { date: longDate(meeting.discussion_on, locale), status: w(`served.meeting_${meeting.status}`) }) : w("served.meeting_none"),
    },
    {
      key: "bundle", name: capitalise(String(vocabulary.court_bundle ?? "")) || w("served.step_bundle"), done: !!bundle && !bundle.stale,
      to: screenPath("trial_pack", { caseId }),
      note: bundle ? (bundle.stale ? w("served.bundle_stale") : w("served.bundle_built", { date: longDate(bundle.built_on, locale) })) : w("served.bundle_none"),
    },
    { key: "trial", name: keyName("kind", "trial"), done: false, note: trial ? w("served.hearing_span", { date: longDate(trial.starts_on, locale), count: days(trial) }) : w("served.trial_none") },
  ];
  const current = track.findIndex((x) => !x.done);

  return (
    <div className="av-page av-after-page av-stack">
      <div className="av-after-head">
        <div>
          <h2 className="av-after-title">{w("served.title")}</h2>
          <p className="av-muted av-after-lede">{w("served.intro", { date: version.served_on ? longDate(version.served_on, locale) : "" })}</p>
        </div>
        <div className="av-after-pill" role="group" aria-label={w("served.locked")}>
          <LockIcon />
          <strong>{w("served.locked")}</strong>
          <button type="button" className="av-after-linkbtn" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? w("served.hide") : w("served.view")}</button>
        </div>
      </div>
      {open && (
        <section className="av-card" aria-label={version.display_name}>
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

      <section className="av-after-trackcard" aria-label={w("served.next")}>
        <ol className="av-after-track">
          {track.map((x, i) => {
            const state = x.done ? "done" : i === current ? "current" : "todo";
            return (
              <li key={x.key} className={`av-after-track__step av-after-track__step--${state}`} aria-current={state === "current" ? "step" : undefined}>
                <span className="av-after-track__dot" aria-hidden="true">{x.done && <TickIcon />}</span>
                <span className="av-after-track__name">{x.to ? <Link to={x.to}>{x.name}</Link> : x.name}</span>
                <span className={x.alert ? "av-after-track__note av-after-track__note--alert" : "av-after-track__note"}>{x.note}</span>
              </li>
            );
          })}
        </ol>
      </section>

      <div className="av-after-tiles">
        <section className="av-after-tile av-after-tile--next" aria-labelledby="served-questions">
          <IconTile><HelpIcon /></IconTile>
          <h3 id="served-questions">{count > 0 ? `${count} ${vocab}` : w("served.questions")}</h3>
          <p>
            {count > 0 && firstRound && left != null
              ? w("served.questions_received", { date: dayMonth(firstRound.received_on, locale), count: left })
              : due ? w("served.questions_body", { date: longDate(due, locale) }) : w("served.questions_none")}
          </p>
          <TextLink to={`/cases/${caseId}/written-questions`}>{w("served.questions_action")}<ArrowIcon /></TextLink>
        </section>
        <section className="av-after-tile" aria-labelledby="served-new">
          <IconTile><NewDocIcon /></IconTile>
          <h3 id="served-new">{w("served.new")}</h3>
          <p>{pages > 0 ? w("served.new_pages", { count: pages }) : w("served.new_body")}</p>
          <TextLink to={`/cases/${caseId}/new-material`}>{w("served.new_action")}<ArrowIcon /></TextLink>
        </section>
        <section className="av-after-tile" aria-labelledby="served-changes">
          <IconTile tone="grey"><PencilIcon /></IconTile>
          <h3 id="served-changes">{w("served.changes")}</h3>
          <p>{made > 0 ? w("served.changes_made", { count: made }) : w("served.changes_body")}</p>
          <TextLink to={`/cases/${caseId}/correct-report`}>{w("served.changes_action")}<ArrowIcon /></TextLink>
        </section>
        <section className="av-after-tile" aria-labelledby="served-orders">
          <IconTile><GavelIcon /></IconTile>
          <h3 id="served-orders">{w("served.orders")}</h3>
          <p>
            {orders > 0
              ? `${w("served.orders_made", { count: orders })}${nextHearing ? ` ${w("served.orders_listed", { kind: keyName("kind", nextHearing.type), date: longDate(nextHearing.starts_on, locale) })}` : ""}`
              : w("served.orders_body")}
          </p>
          <TextLink to={`/cases/${caseId}/court-orders`}>{w("served.orders_action")}<ArrowIcon /></TextLink>
        </section>
      </div>
    </div>
  );
}
