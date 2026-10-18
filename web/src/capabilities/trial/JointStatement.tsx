// S22 Joint statement (docs/design/screens/S22-JointStatement.dc.html): set up the meeting from the pack's
// meeting types, read the agenda, answer each question with your position from the served report, and
// prepare the statement for signing. A signed statement is read-only. The server decides every step.
import { useEffect, useId, useState } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { longDate, pdfBody, saveBlob, tag, vocab } from "./data";
import { PdfPicker, TrialFrame, TrialHeader, type Frame } from "./frame";
import { w } from "./words";

type Meeting = Schemas["MeetingOut"];
type Item = Schemas["ItemOut"];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const SCREEN = "expert_meeting";
const READ_MS = 3000;
const ERRORS = {
  change_note_needed: "trial.meeting.error.change_note_needed",
  no_items: "trial.meeting.error.no_items",
  precondition_failed: "trial.meeting.error.stale",
  stale: "trial.meeting.error.stale",
  not_served: "trial.meeting.error.not_served",
  locked: "trial.meeting.error.locked",
};
const LOCKED = new Set(["signed", "signed_external"]);

export function JointStatement() {
  return <TrialFrame>{(frame) => <Meetings frame={frame} />}</TrialFrame>;
}

function Meetings({ frame }: { frame: Frame }) {
  const path = { case_id: frame.caseId };
  const list = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/expert-meetings", { params: { path } })), [frame.caseId]);
  if (list.error) return <div className="av-page"><LoadError onRetry={list.reload} /></div>;
  if (!list.data) return <div className="av-page"><Loading /></div>;
  const meeting = list.data.meetings[0];
  if (!meeting) return <SetUp frame={frame} onDone={list.reload} />;
  return <MeetingScreen frame={frame} meetingId={meeting.id} />;
}

function SetUp({ frame, onDone }: { frame: Frame; onDone: () => void }) {
  const path = { case_id: frame.caseId };
  const types = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/expert-meetings/types", { params: { path } })), [frame.caseId]);
  const selectId = useId();
  const [kind, setKind] = useState("");
  const [on, setOn] = useState("");
  const [other, setOther] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);
  const options = types.data?.types ?? [];
  const chosen = kind || options[0]?.key || "";

  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await unwrap(
        api.POST("/v1/cases/{case_id}/expert-meetings", {
          params: { path },
          body: { type: chosen, discussion_on: on, participants: other.trim() ? [{ name: other.trim() }] : [] },
        }),
      );
      onDone();
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    } finally {
      setBusy(false);
    }
  };

  if (types.error) return <div className="av-page"><LoadError onRetry={types.reload} /></div>;
  if (!types.data) return <div className="av-page"><Loading /></div>;
  return (
    <div className="av-page av-stack">
      <TrialHeader pack={frame.pack} screen={SCREEN} title={w("meeting.setup_title", { meeting: vocab(frame.pack, "expert_meeting") })} />
      <p>{w("meeting.setup_lead")}</p>
      {message && <Notice tone={message.tone === "error" ? "error" : "info"}><p>{message.text}</p></Notice>}
      <div className="av-field">
        <label className="av-field__label" htmlFor={selectId}>{w("meeting.type")}</label>
        <select id={selectId} className="av-input" value={chosen} onChange={(e) => setKind(e.target.value)}>
          {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
      </div>
      <TextField label={w("meeting.discussion_on")} name="discussion_on" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
      <TextField label={w("meeting.other_expert")} name="other_expert" value={other} onChange={(e) => setOther(e.target.value)} />
      <div className="av-trial-actions">
        <Button onClick={submit} disabled={busy || !frame.can("case.edit_setup") || !chosen || !on}>{w("meeting.setup")}</Button>
      </div>
    </div>
  );
}

function MeetingScreen({ frame, meetingId }: { frame: Frame; meetingId: string }) {
  const path = { case_id: frame.caseId, meeting_id: meetingId };
  const stepUp = useStepUp();
  const detail = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/expert-meetings/{meeting_id}", { params: { path } })), [meetingId]);
  const people = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/expert-meetings/{meeting_id}/participants", { params: { path } })), [meetingId]);
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [reading, setReading] = useState(false);
  const [prepared, setPrepared] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (detail.data) setMeeting(detail.data);
  }, [detail.data]);

  // While the agenda is read the questions are not there yet: look again every few seconds.
  const waiting = reading && (meeting?.items.length ?? 0) === 0;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(detail.reload, READ_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting]);

  if (detail.error) return <div className="av-page"><LoadError onRetry={detail.reload} /></div>;
  if (!meeting) return <div className="av-page"><Loading /></div>;

  const locale = frame.locale;
  const locked = LOCKED.has(meeting.status);
  const names = (people.data?.participants ?? []).map((p) => p.name).join(", ");
  const pages = prepared ?? meeting.prepared_pages;
  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });

  const addAgenda = async (file: File) => {
    setMessage(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/expert-meetings/{meeting_id}/agenda", { params: { path }, ...(await pdfBody(file)) }));
      setReading(true);
    } catch (error) {
      fail(error);
    }
  };

  const prepare = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const done = await unwrap(api.POST("/v1/cases/{case_id}/expert-meetings/{meeting_id}/prepare", { params: { path } }));
      setPrepared(done.pages);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    setMessage(null);
    try {
      const blob = await stepUp(() =>
        unwrap(api.POST("/v1/cases/{case_id}/expert-meetings/{meeting_id}/send-for-signature", { params: { path }, parseAs: "blob" })),
      );
      if (!blob) return;
      saveBlob(blob as Blob, "statement-for-signing.pdf");
      setMessage({ tone: "info", text: w("meeting.out_for_signature") });
    } catch (error) {
      fail(error);
    }
  };

  const addSigned = async (file: File) => {
    setMessage(null);
    try {
      const body = await pdfBody(file);
      const next = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/expert-meetings/{meeting_id}/signed-external", { params: { path }, ...body })));
      if (next) detail.reload();
    } catch (error) {
      fail(error);
    }
  };

  const replace = (next: Item) => setMeeting((m) => (m ? { ...m, items: m.items.map((i) => (i.id === next.id ? next : i)) } : m));
  const agenda = meeting.agenda_versions.at(-1);

  return (
    <div className="av-page av-stack">
      <TrialHeader pack={frame.pack} screen={SCREEN} title={w("meeting.heading", { label: meeting.label, names })} />
      <p>{w("meeting.lead", { date: longDate(meeting.discussion_on, locale) })}</p>
      <dl className="av-trial-facts">
        {meeting.sign_by && (
          <>
            <dt>{w("meeting.sign_by")}</dt>
            <dd>{[longDate(meeting.sign_by, locale), meeting.sign_rule_ref].filter(Boolean).join(" · ")}</dd>
          </>
        )}
        {meeting.send_by && (
          <>
            <dt>{w("meeting.send_by")}</dt>
            <dd>{[longDate(meeting.send_by, locale), meeting.send_rule_ref].filter(Boolean).join(" · ")}</dd>
          </>
        )}
      </dl>
      {meeting.status === "signed" && <Notice tone="success"><p>{w("meeting.signed")}</p></Notice>}
      {meeting.status === "signed_external" && <Notice tone="success"><p>{w("meeting.signed_elsewhere")}</p></Notice>}
      {meeting.status === "out_for_signature" && !message && <Notice><p>{w("meeting.out_for_signature")}</p></Notice>}
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}

      {meeting.items.length === 0 ? (
        <div className="av-stack">
          <p>{reading ? w("meeting.agenda.reading") : w("meeting.agenda.none")}</p>
          {!locked && frame.can("case.upload") && <PdfPicker label={w("meeting.agenda.add")} onFile={addAgenda} disabled={reading} />}
        </div>
      ) : (
        <>
          {agenda && <p className="av-trial-muted">{w("meeting.agenda_line", { version: agenda.version_no, count: meeting.items.length })}</p>}
          {meeting.items.map((i) => (
            <Question key={i.id} caseId={frame.caseId} item={i} locked={locked || !frame.can("opinion.write")} onSaved={replace} />
          ))}
          {!locked && frame.can("case.upload") && <PdfPicker label={w("meeting.agenda.new")} onFile={addAgenda} />}
        </>
      )}

      <Declarations lines={meeting.declarations} />

      {!locked && meeting.items.length > 0 && (
        <div className="av-trial-actions">
          {frame.can("report.sign") && <PdfPicker label={w("meeting.add_signed")} onFile={addSigned} />}
          {pages !== null && pages !== undefined && <p>{w("meeting.prepared", { pages })}</p>}
          {pages !== null && pages !== undefined && frame.can("report.sign") && (
            <Button variant="secondary" onClick={download}>{w("meeting.download_for_signing")}</Button>
          )}
          {frame.can("opinion.write") && <Button onClick={prepare} disabled={busy}>{w("meeting.prepare")}</Button>}
        </div>
      )}
    </div>
  );
}

function Declarations({ lines }: { lines: string[] }) {
  const id = useId();
  if (lines.length === 0) return null;
  return (
    <section aria-labelledby={id} className="av-card av-stack">
      <h2 id={id}>{w("meeting.declarations.heading")}</h2>
      <ul className="av-trial-list">
        {lines.map((line) => <li key={line}>{line}</li>)}
      </ul>
    </section>
  );
}

function Question({ caseId, item, locked, onSaved }: { caseId: string; item: Item; locked: boolean; onSaved: (item: Item) => void }) {
  const id = useId();
  const [own, setOwn] = useState(item.own_position ?? "");
  const [other, setOther] = useState(item.other_positions ?? "");
  const [agreed, setAgreed] = useState<boolean | null>(item.agreed);
  const [reason, setReason] = useState(item.reason ?? "");
  const [note, setNote] = useState(item.change_note ?? "");
  const [state, setState] = useState<Message>(null);
  const legend = `${w("meeting.item.question", { number: item.number })}: ${item.text}`;

  const save = async () => {
    setState(null);
    const change: Schemas["ItemChange"] = {};
    if (own !== (item.own_position ?? "")) change.own_position = own;
    if (other !== (item.other_positions ?? "")) change.other_positions = other;
    if (agreed !== item.agreed) change.agreed = agreed;
    if (reason !== (item.reason ?? "")) change.reason = reason;
    if (note !== (item.change_note ?? "")) change.change_note = note;
    try {
      const next = await unwrap(
        api.PATCH("/v1/cases/{case_id}/agenda-items/{item_id}", {
          params: { path: { case_id: caseId, item_id: item.id } },
          headers: ifMatch(tag(item.version)),
          body: change,
        }),
      );
      onSaved(next);
      setState({ tone: "success", text: w("meeting.item.saved") });
    } catch (error) {
      setState({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };

  const paras = item.served_paragraphs.join(", ");
  return (
    <fieldset className="av-card av-stack av-trial-question">
      <legend className="av-trial-question__legend">{legend}</legend>
      {paras && <p className="av-trial-muted">{w("meeting.item.in_report", { paras })}</p>}
      {item.deviation === "changed" && <Notice tone="warning"><p>{w("meeting.item.changed")}</p></Notice>}
      {item.deviation === "unclear" && <Notice tone="warning"><p>{w("meeting.item.unclear")}</p></Notice>}
      {locked ? (
        <dl className="av-trial-facts">
          <dt>{w("meeting.item.yours")}</dt>
          <dd className="av-trial-prose">{item.own_position}</dd>
          <dt>{w("meeting.item.theirs")}</dt>
          <dd className="av-trial-prose">{item.other_positions}</dd>
          <dt>{w("meeting.item.agreed")}</dt>
          <dd>{item.agreed === null ? "" : item.agreed ? w("meeting.item.yes") : w("meeting.item.no")}</dd>
          {item.reason && (
            <>
              <dt>{w("meeting.item.reason")}</dt>
              <dd className="av-trial-prose">{item.reason}</dd>
            </>
          )}
        </dl>
      ) : (
        <>
          <Area id={`${id}-own`} label={w("meeting.item.yours")} value={own} onChange={setOwn} />
          <Area id={`${id}-other`} label={w("meeting.item.theirs")} value={other} onChange={setOther} />
          <fieldset className="av-trial-choice">
            <legend className="av-field__label">{w("meeting.item.agreed")}</legend>
            <label className="av-trial-radio">
              <input type="radio" name={`${id}-agreed`} checked={agreed === true} onChange={() => setAgreed(true)} />
              <span>{w("meeting.item.yes")}</span>
            </label>
            <label className="av-trial-radio">
              <input type="radio" name={`${id}-agreed`} checked={agreed === false} onChange={() => setAgreed(false)} />
              <span>{w("meeting.item.no")}</span>
            </label>
          </fieldset>
          {agreed === false && <Area id={`${id}-reason`} label={w("meeting.item.reason")} value={reason} onChange={setReason} short />}
          {item.deviation === "changed" && <Area id={`${id}-note`} label={w("meeting.item.change_note")} value={note} onChange={setNote} short />}
          {state && <Notice tone={state.tone}><p>{state.text}</p></Notice>}
          <div className="av-trial-actions">
            <Button variant="secondary" onClick={save}>{w("meeting.item.save")}</Button>
          </div>
        </>
      )}
    </fieldset>
  );
}

function Area({ id, label, value, onChange, short }: { id: string; label: string; value: string; onChange: (v: string) => void; short?: boolean }) {
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>{label}</label>
      <textarea id={id} className={`av-textarea${short ? " av-trial-short" : ""}`} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
