// S12 One consultation, as the clinician saw it. The title (with the clinician's role) and the guidance in force
// come from the API (the pack and the seeded table); later entries are counted and folded away; entries with no
// known arrival are shown as not known. The expert keeps a note and may change when something arrived, with a reason.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Dialog, LoadError, Loading, Notice, StatusBadge } from "../../foundation/ui";
import { Comments } from "./Comments";
import { kindWord, longDate, pageLabel, statusOf, TextArea } from "./shared";
import { w } from "./words";

type Entry = Schemas["Entry"];

function Arrival({ entry }: { entry: Entry }) {
  return <span className="av-muted">{entry.available_at ? w("consultation.arrived", { date: longDate(entry.available_at) }) : w("consultation.arrival_unknown")}</span>;
}

function Entries({ items, onChange }: { items: Entry[]; onChange: (e: Entry) => void }) {
  return (
    <ul className="av-understand-list">
      {items.map((e) => (
        <li key={e.fact_id} className="av-understand-event">
          <span>
            {kindWord(e.kind)} · {longDate(e.event_date)}
          </span>
          <span className="av-muted">{pageLabel(e.page_no)}</span>
          <StatusBadge status={statusOf(e.status)} />
          <Arrival entry={e} />
          <Button variant="secondary" onClick={() => onChange(e)}>
            {w("consultation.change_arrival")}
          </Button>
        </li>
      ))}
    </ul>
  );
}

function ArrivalDialog({ caseId, entry, onClose, onSaved }: { caseId: string; entry: Entry; onClose: () => void; onSaved: () => void }) {
  const [date, setDate] = useState("");
  const [unknown, setUnknown] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const ready = reason.trim() !== "" && (unknown || date !== "");
  const save = async () => {
    setError(null);
    try {
      await unwrap(
        api.PATCH("/v1/cases/{case_id}/facts/{fact_id}/availability", {
          params: { path: { case_id: caseId, fact_id: entry.fact_id } },
          body: { available_at: unknown ? null : date, unknown, reason: reason.trim() },
        }),
      );
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog title={w("consultation.arrival_title")} onClose={onClose}>
      <form
        className="av-stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) void save();
        }}
      >
        <p className="av-muted">{w("consultation.arrival_hint")}</p>
        <div className="av-field">
          <label className="av-field__label" htmlFor="arrival-date">{w("consultation.arrival_date")}</label>
          <input id="arrival-date" className="av-input" type="date" value={date} disabled={unknown} onChange={(e) => setDate(e.target.value)} />
        </div>
        <label className="av-checkbox">
          <input type="checkbox" checked={unknown} onChange={(e) => setUnknown(e.target.checked)} />
          <span>{w("consultation.arrival_unknown_box")}</span>
        </label>
        <TextArea label={w("consultation.arrival_reason")} rows={3} required value={reason} onChange={(e) => setReason(e.target.value)} />
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <div className="av-row">
          <Button type="submit" disabled={!ready}>{w("common.save")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("common.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function NoteEditor({ caseId, factId, initial }: { caseId: string; factId: string; initial: string }) {
  const [body, setBody] = useState(initial);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    setSaved(false);
    try {
      await unwrap(api.PUT("/v1/cases/{case_id}/consultations/{fact_id}/note", { params: { path: { case_id: caseId, fact_id: factId } }, body: { body } }));
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <section aria-labelledby="consult-note" className="av-card av-stack">
      <h2 id="consult-note">{w("consultation.note_title")}</h2>
      <TextArea label={w("consultation.note")} hint={w("consultation.note_hint")} rows={5} value={body} onChange={(e) => { setBody(e.target.value); setSaved(false); }} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {saved && <Notice tone="success"><p>{w("consultation.note_saved")}</p></Notice>}
      <div>
        <Button onClick={() => void save()}>{w("consultation.note_save")}</Button>
      </div>
    </section>
  );
}

export function Consultation() {
  const { caseId = "", factId = "" } = useParams();
  const [changing, setChanging] = useState<Entry | null>(null);
  const found = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/consultations/{fact_id}", { params: { path: { case_id: caseId, fact_id: factId }, query: { as_seen: true } } })),
    [caseId, factId],
  );
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const c = found.data;
  const day = c.entry.event_date ?? c.entry.available_at;
  return (
    <div className="av-page av-stack">
      <div>
        <Link to={`/cases/${caseId}/timeline`}>← {w("common.back_to_timeline")}</Link>
        <h2>{w("consultation.on", { title: c.title, date: longDate(day) })}</h2>
        <p className="av-muted">
          {kindWord(c.entry.kind)} · {pageLabel(c.entry.page_no)} · {w("consultation.hint")}
        </p>
      </div>
      <section aria-labelledby="consult-seen" className="av-card av-stack">
        <h2 id="consult-seen">{w("consultation.seen")}</h2>
        {c.visible.length === 0 ? <p className="av-muted">{w("consultation.seen_none")}</p> : <Entries items={c.visible} onChange={setChanging} />}
        <details className="av-understand-later">
          <summary>{w("consultation.later", { count: c.later_count })}</summary>
          {c.later.length === 0 ? <p className="av-muted">{w("consultation.later_none")}</p> : <Entries items={c.later} onChange={setChanging} />}
        </details>
      </section>
      {c.unknown.length > 0 && (
        <section aria-labelledby="consult-unknown" className="av-card av-stack">
          <h2 id="consult-unknown">{w("consultation.unknown")}</h2>
          <p className="av-muted">{w("consultation.unknown_hint")}</p>
          <Entries items={c.unknown} onChange={setChanging} />
        </section>
      )}
      <section aria-labelledby="consult-guidance" className="av-card av-stack">
        <h2 id="consult-guidance">{w("consultation.guidance")}</h2>
        {c.guidance.length === 0 && <p className="av-muted">{w("consultation.guidance_none")}</p>}
        <ul className="av-understand-list">
          {c.guidance.map((g) => {
            const name = `${g.title ?? g.code} (${g.code})`;
            return (
              <li key={`${g.code}-${g.version_label}`} className="av-understand-event">
                {g.url ? <a href={g.url} target="_blank" rel="noopener noreferrer">{name}</a> : <span>{name}</span>}
                <span className="av-muted">{g.version_label}</span>
              </li>
            );
          })}
        </ul>
      </section>
      <NoteEditor key={`${factId}-${c.note ?? ""}`} caseId={caseId} factId={factId} initial={c.note ?? ""} />
      <Comments caseId={caseId} targetKind="fact" targetId={factId} />
      {changing && (
        <ArrivalDialog
          caseId={caseId}
          entry={changing}
          onClose={() => setChanging(null)}
          onSaved={() => {
            setChanging(null);
            found.reload();
          }}
        />
      )}
    </div>
  );
}
