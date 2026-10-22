// S22 Joint statement (docs/design/screens/S22-JointStatement.dc.html): set up the meeting from the pack's
// meeting types, read the agenda, see each question with your position from the served report beside the
// other expert's, and prepare the statement for signing. Each row's editor opens behind "Change". A signed
// statement is read-only. The server decides every step.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { BackLink, Button, ChoicePills, Checkbox, LoadError, Loading, Notice, PageRef, TextField } from "../../foundation/ui";
import { longDate, pdfBody, saveBlob, shortDate, tag, vocab } from "./data";
import { PdfPicker, TrialFrame, TrialPage, TrialTitle, type Frame } from "./frame";
import { w, wn } from "./words";

type Meeting = Schemas["MeetingOut"];
type Item = Schemas["ItemOut"];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

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
    <TrialPage width="s22">
      <TrialTitle title={w("meeting.setup_title", { meeting: vocab(frame.pack, "expert_meeting") })}>
        <p className="av-trial-lead">{w("meeting.setup_lead")}</p>
      </TrialTitle>
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
    </TrialPage>
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
  const [adding, setAdding] = useState(false);

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
  const participants = people.data?.participants ?? [];
  const names = participants.map((p) => p.name).join(", ");
  // "Dr R (for the instructing party)": the side's word comes from the pack's party roles.
  const sideWord = (side: string | null) => frame.pack.party_roles.find((r) => r.key === side)?.label;
  const withNames = participants.map((p) => (sideWord(p.side) ? `${p.name} ${w("meeting.for_side", { party: sideWord(p.side) })}` : p.name)).join(", ");
  const reworded = meeting.items.filter((i) => i.changed_from_previous === "changed").map((i) => i.number);
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
  const canEdit = !locked && frame.can("opinion.write");

  return (
    <TrialPage width="s22">
      <div className="av-trial-head">
        <div className="av-trial-head__text">
          <BackLink to={screenPath("after_serving", { caseId: frame.caseId }) ?? `/cases/${frame.caseId}`}>{w("meeting.back")}</BackLink>
          <TrialTitle title={w("meeting.heading", { label: meeting.label, names })}>
            <p className="av-trial-lead">{w("meeting.lead", { date: longDate(meeting.discussion_on, locale) })}</p>
            <p className="av-trial-muted av-trial-line" data-testid="meeting-line">
              {[
                withNames && w("meeting.with", { names: withNames }),
                agenda &&
                  [
                    w("meeting.agenda_received", { version: agenda.version_no, date: shortDate(agenda.created_at, locale) }),
                    reworded.length > 0 && wn("meeting.reworded", reworded.length, { numbers: reworded.join(", ") }),
                  ].filter(Boolean).join(": "),
              ].filter(Boolean).join(" · ")}
              {!locked && frame.can("case.edit_setup") && (
                <>
                  {" · "}
                  <button type="button" className="av-text-link av-trial-linkbutton" onClick={() => setAdding(true)} aria-expanded={adding}>
                    {w("meeting.add_expert")}
                  </button>
                </>
              )}
            </p>
          </TrialTitle>
        </div>
        <ul className="av-trial-chips">
          {meeting.sign_by && <Deadline label={w("meeting.sign_by")} date={shortDate(meeting.sign_by, locale)} rule={meeting.sign_rule_phrase || meeting.sign_rule_ref} />}
          {meeting.send_by && <Deadline label={w("meeting.send_by")} date={shortDate(meeting.send_by, locale)} rule={meeting.send_rule_phrase || meeting.send_rule_ref} />}
          {/* Beside the deadlines, where the board leaves room, so its line of text keeps its length. */}
          {!locked && meeting.items.length > 0 && frame.can("case.upload") && (
            <li className="av-trial-chips__action"><PdfPicker link label={w("meeting.agenda.new")} onFile={addAgenda} /></li>
          )}
        </ul>
      </div>
      {adding && <AddExpert path={path} onDone={() => { setAdding(false); people.reload(); }} onCancel={() => setAdding(false)} />}
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
        <Questions caseId={frame.caseId} items={meeting.items} names={names} canEdit={canEdit} onSaved={replace} />
      )}

      <Declarations
        lines={meeting.declarations}
        footer={
          <p className="av-trial-muted">
            {w("meeting.footnote")}{" "}
            {!locked && meeting.items.length > 0 && frame.can("report.sign") && <PdfPicker link label={w("meeting.add_signed")} onFile={addSigned} />}
          </p>
        }
      />

      {!locked && meeting.items.length > 0 && (
        <div className="av-trial-actions">
          {pages !== null && pages !== undefined && <p>{w("meeting.prepared", { pages })}</p>}
          {pages !== null && pages !== undefined && frame.can("report.sign") && (
            <Button variant="secondary" onClick={download}>{w("meeting.download_for_signing")}</Button>
          )}
          {frame.can("opinion.write") && <Button className="av-trial-cta" onClick={prepare} disabled={busy}>{w("meeting.prepare")}</Button>}
        </div>
      )}
    </TrialPage>
  );
}

function Deadline({ label, date, rule }: { label: string; date: string; rule: string }) {
  return (
    <li className="av-trial-chip">
      <span><span className="av-trial-strong">{label}</span> {date}</span>
      {rule && <span className="av-trial-chip__rule">{rule}</span>}
    </li>
  );
}

function AddExpert({ path, onDone, onCancel }: { path: { case_id: string; meeting_id: string }; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const save = async () => {
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/expert-meetings/{meeting_id}/participants", { params: { path }, body: { name: name.trim() } }));
      onDone();
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };
  return (
    <div className="av-card av-stack">
      {message && <Notice tone={message.tone === "error" ? "error" : "info"}><p>{message.text}</p></Notice>}
      <TextField label={w("meeting.other_expert")} name="add_expert" value={name} onChange={(e) => setName(e.target.value)} />
      <div className="av-trial-actions">
        <Button variant="secondary" onClick={onCancel}>{w("meeting.add_expert_cancel")}</Button>
        <Button onClick={save} disabled={!name.trim()}>{w("meeting.add_expert_save")}</Button>
      </div>
    </div>
  );
}

function Declarations({ lines, footer }: { lines: string[]; footer: ReactNode }) {
  const id = useId();
  if (lines.length === 0) return null;
  return (
    <section aria-labelledby={id} className="av-card av-stack av-trial-decl">
      <h2 id={id}>{w("meeting.declarations.heading")}</h2>
      <ul className="av-trial-list av-trial-list--plain">
        {lines.map((line) => <li key={line}><Checkbox label={line} checked disabled readOnly /></li>)}
      </ul>
      {footer}
    </section>
  );
}

/** The agenda as the board draws it: one row per question, your position beside the other expert's. */
function Questions({ caseId, items, names, canEdit, onSaved }: { caseId: string; items: Item[]; names: string; canEdit: boolean; onSaved: (item: Item) => void }) {
  const [open, setOpen] = useState<{ id: string; focus: "note" | null } | null>(null);
  return (
    <section className="av-card av-trial-grid-card">
      <table className="av-trial-grid">
        <thead>
          <tr>
            <th scope="col">{w("meeting.col.question")}</th>
            <th scope="col">{w("meeting.col.yours")}</th>
            <th scope="col">{names ? w("meeting.col.theirs", { names }) : w("meeting.item.theirs")}</th>
            <th scope="col">{w("meeting.item.pages")}</th>
            <th scope="col">{w("meeting.item.agreed")}</th>
          </tr>
        </thead>
        <tbody>
          {items.flatMap((item) => {
            const editing = open?.id === item.id;
            const rows = [<Row key={item.id} item={item} canEdit={canEdit} onChange={(focus) => setOpen({ id: item.id, focus })} />];
            if (editing && canEdit) {
              rows.push(
                <tr key={`${item.id}-edit`} className="av-trial-editrow">
                  <td colSpan={5}>
                    <Editor caseId={caseId} item={item} focus={open.focus} onSaved={onSaved} onClose={() => setOpen(null)} />
                  </td>
                </tr>,
              );
            }
            return rows;
          })}
        </tbody>
      </table>
    </section>
  );
}

function Row({ item, canEdit, onChange }: { item: Item; canEdit: boolean; onChange: (focus: "note" | null) => void }) {
  const paras = item.served_paragraphs.join(", ");
  const changed = item.deviation === "changed";
  const rowId = useId();
  return (
    <tr className={`av-trial-row${changed ? " av-trial-row--changed" : ""}`}>
      <th scope="row">
        <span id={rowId}>{item.number}. {item.text}</span>
        {/* Under the question, where the board leaves room, so the rows keep the board's heights. */}
        {canEdit && !changed && (
          <button type="button" className="av-text-link av-trial-linkbutton" onClick={() => onChange(null)} aria-describedby={rowId}>{w("meeting.item.change")}</button>
        )}
      </th>
      <td>
        <div className="av-stack av-trial-cell">
          <p>
            <span>{item.own_position}</span>{" "}
            {paras && !changed && <small className="av-trial-muted">{w("meeting.item.para", { paras })}</small>}
          </p>
          {changed && (
            <p className="av-trial-differs">
              <strong>{w("meeting.item.changed_lead")}</strong>{" "}
              {item.served_text && <>{w("meeting.item.changed_was", { text: item.served_text, paras })} </>}
              {w("meeting.item.changed_rest")}
            </p>
          )}
          {item.deviation === "unclear" && <p className="av-trial-differs">{w("meeting.item.unclear")}</p>}
        </div>
      </td>
      <td>{item.other_positions}</td>
      <td>
        <div className="av-trial-pagelist">
          {item.pages.map((n) => <PageRef key={n} page={n} />)}
        </div>
      </td>
      <td>
        <div className="av-trial-agreed">
          {item.agreed !== null && <span className={`av-trial-tag${item.agreed ? " av-trial-tag--yes" : ""}`}>{item.agreed ? w("meeting.item.yes") : w("meeting.item.no")}</span>}
          {item.agreed === false && item.reason && <small className="av-trial-muted">{w("meeting.item.reason_shown", { reason: item.reason })}</small>}
          {canEdit && changed && (
            <button type="button" className="av-text-link av-trial-linkbutton" onClick={() => onChange("note")} aria-describedby={rowId}>{w("meeting.item.add_note")}</button>
          )}
        </div>
      </td>
    </tr>
  );
}

function Editor({ caseId, item, focus, onSaved, onClose }: { caseId: string; item: Item; focus: "note" | null; onSaved: (item: Item) => void; onClose: () => void }) {
  const id = useId();
  const [own, setOwn] = useState(item.own_position ?? "");
  const [other, setOther] = useState(item.other_positions ?? "");
  const [agreed, setAgreed] = useState<boolean | null>(item.agreed);
  const [reason, setReason] = useState(item.reason ?? "");
  const [note, setNote] = useState(item.change_note ?? "");
  const [state, setState] = useState<Message>(null);
  const noteField = useRef<HTMLTextAreaElement>(null);
  const legend = `${w("meeting.item.question", { number: item.number })}: ${item.text}`;

  useEffect(() => {
    if (focus === "note") noteField.current?.focus();
  }, [focus]);

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

  return (
    <fieldset className="av-stack av-trial-editor">
      <legend className="av-trial-question__legend">{legend}</legend>
      <Area id={`${id}-own`} label={w("meeting.item.yours")} value={own} onChange={setOwn} />
      <Area id={`${id}-other`} label={w("meeting.item.theirs")} value={other} onChange={setOther} />
      <ChoicePills
        label={w("meeting.item.agreed")}
        name={`${id}-agreed`}
        value={agreed === null ? null : agreed ? "yes" : "no"}
        onChange={(v) => setAgreed(v === "yes")}
        options={[{ value: "yes", label: w("meeting.item.yes") }, { value: "no", label: w("meeting.item.no") }]}
      />
      {agreed === false && <Area id={`${id}-reason`} label={w("meeting.item.reason")} value={reason} onChange={setReason} short />}
      {item.deviation === "changed" && <Area id={`${id}-note`} label={w("meeting.item.change_note")} value={note} onChange={setNote} short field={noteField} />}
      {state && <Notice tone={state.tone}><p>{state.text}</p></Notice>}
      <div className="av-trial-actions">
        <Button variant="quiet" onClick={onClose}>{w("meeting.item.close")}</Button>
        <Button variant="secondary" onClick={save}>{w("meeting.item.save")}</Button>
      </div>
    </fieldset>
  );
}

function Area({ id, label, value, onChange, short, field }: { id: string; label: string; value: string; onChange: (v: string) => void; short?: boolean; field?: React.Ref<HTMLTextAreaElement> }) {
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>{label}</label>
      <textarea id={id} ref={field} className={`av-textarea${short ? " av-trial-short" : ""}`} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
