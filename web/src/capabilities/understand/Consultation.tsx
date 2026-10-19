// S12 One consultation, as the clinician saw it. The title (with the clinician's role) and the guidance in force
// come from the API (both from the case's packs); later entries are counted and folded away; entries with no
// known arrival are shown as not known. Any day can be chosen. Each person keeps a private note, and the named
// expert may change when something arrived, with a reason. Buttons follow what the person may do on the case.
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, DictationBox, Dialog, LoadError, Loading, Notice, QChip, StatusBadge } from "../../foundation/ui";
import { Comments } from "./Comments";
import { GuidanceChanged, kindWord, longDate, pageLabel, statusOf, TextArea, useCan } from "./shared";
import { w } from "./words";
import { pageImage } from "./pageImage";

type Entry = Schemas["Entry"];

function Arrival({ entry }: { entry: Entry }) {
  return <span className="av-muted">{entry.available_at ? w("consultation.arrived", { date: longDate(entry.available_at) }) : w("consultation.arrival_unknown")}</span>;
}

function Entries({ items, onChange }: { items: Entry[]; onChange?: (e: Entry) => void }) {
  return (
    <ul className="av-understand-list">
      {items.map((e) => (
        <li key={e.fact_id} className="av-understand-event">
          <span>
            {kindWord(e.kind)} · {longDate(e.event_date)}
          </span>
          <span className="av-muted">{pageLabel(e.page_no)}</span>
          {e.summary && <span>{e.summary}</span>}
          <StatusBadge status={statusOf(e.status)} />
          {e.has_finding && <StatusBadge status="look" />}
          <Arrival entry={e} />
          {onChange && (
            <Button variant="secondary" onClick={() => onChange(e)}>
              {w("consultation.change_arrival")}
            </Button>
          )}
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

function NoteEditor({ caseId, factId, initial, canAdd }: { caseId: string; factId: string; initial: string; canAdd: boolean }) {
  const [body, setBody] = useState(initial);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const save = async (thenLeave: boolean) => {
    setError(null);
    setSaved(false);
    try {
      await unwrap(api.PUT("/v1/cases/{case_id}/consultations/{fact_id}/note", { params: { path: { case_id: caseId, fact_id: factId } }, body: { body } }));
      setSaved(true);
      if (thenLeave) navigate(`/cases/${caseId}/timeline`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <section aria-labelledby="consult-note" className="av-card av-stack">
      <h2 id="consult-note">{w("consultation.note_title")}</h2>
      <p className="av-muted" id="consult-note-hint">{w("consultation.note_hint")}</p>
      {(canAdd || initial) && (
        <DictationBox caseId={caseId} context="consultation_note" label={w("consultation.note")} rows={5} bar value={body} onChange={(v) => { setBody(v); setSaved(false); }} />
      )}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {saved && <Notice tone="success"><p>{w("consultation.note_saved")}</p></Notice>}
      {(canAdd || initial) && (
        <div className="av-row">
          <Button variant="secondary" onClick={() => void save(false)}>{w("consultation.note_save")}</Button>
          <Button onClick={() => void save(true)}>{w("consultation.note_continue")}</Button>
        </div>
      )}
    </section>
  );
}

function PagePanel({ caseId, pageNo }: { caseId: string; pageNo: number }) {
  const shown = useResource(() => pageImage(caseId, pageNo), [caseId, pageNo]);
  return (
    <section aria-labelledby="consult-page" className="av-card av-stack av-understand-page">
      <h2 id="consult-page">{w("consultation.page_title")}</h2>
      {shown.error ? (
        <p className="av-muted">{w("consultation.page_none")}</p>
      ) : shown.data ? (
        <img src={shown.data} alt={w("consultation.page_alt", { page: pageNo })} />
      ) : (
        <p className="av-muted">{w("consultation.page_loading")}</p>
      )}
    </section>
  );
}

function LaterNotes({ caseId, factId }: { caseId: string; factId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/consultations/{fact_id}/later-notes", { params: { path: { case_id: caseId, fact_id: factId } } })), [caseId, factId]);
  return (
    <section aria-labelledby="consult-later-notes" className="av-card av-stack">
      <h2 id="consult-later-notes">{w("consultation.later_notes_title")}</h2>
      <p className="av-muted">{w("consultation.later_notes_hint")}</p>
      {found.error ? (
        <LoadError onRetry={found.reload} />
      ) : !found.data ? (
        <Loading />
      ) : found.data.notes.length === 0 ? (
        <p className="av-muted">{w("consultation.later_notes_none")}</p>
      ) : (
        <ul className="av-understand-list">
          {found.data.notes.map((n) => (
            <li key={n.fact_id} className="av-understand-event">
              <span>
                {longDate(n.event_date)} · {kindWord(n.kind)}
              </span>
              <span className="av-muted">{pageLabel(n.page_no)}</span>
              {n.has_note && <span>{w("consultation.later_notes_mine")}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Consultation() {
  const { caseId = "", factId = "" } = useParams();
  const [changing, setChanging] = useState<Entry | null>(null);
  const [on, setOn] = useState<string | null>(null);
  const [typed, setTyped] = useState<string | null>(null);
  const can = useCan(caseId);
  const found = useResource(
    () =>
      unwrap(
        api.GET("/v1/cases/{case_id}/consultations/{fact_id}", {
          params: { path: { case_id: caseId, fact_id: factId }, query: on ? { as_seen: true, on } : { as_seen: true } },
        }),
      ),
    [caseId, factId, on],
  );
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const c = found.data;
  const day = c.entry.event_date ?? c.entry.available_at;
  const change = can("availability.override") ? setChanging : undefined;
  const pick = (value: string) => {
    setTyped(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) setOn(value);
  };
  return (
    <div className="av-page av-stack">
      <div>
        <BackLink to={`/cases/${caseId}/timeline`}>{w("common.back_to_timeline")}</BackLink>
        <h2>{w("consultation.on", { title: c.title, date: longDate(day) })}</h2>
        <p className="av-muted">
          {kindWord(c.entry.kind)} · {pageLabel(c.entry.page_no)} · {w("consultation.hint")}
        </p>
        {c.question_numbers.length > 0 && (
          <div role="group" aria-label={w("consultation.linked_to")} className="av-understand-chips">
            {c.question_numbers.map((n) => (
              <QChip key={n} n={n} />
            ))}
          </div>
        )}
      </div>
      <GuidanceChanged codes={c.guidance_changed} />
      <div className="av-field">
        <label className="av-field__label" htmlFor="consult-on">{w("consultation.on_label")}</label>
        <input id="consult-on" className="av-input" type="date" value={typed ?? c.on} onChange={(e) => pick(e.target.value)} />
      </div>
      <div className="av-understand-split">
      <div className="av-stack">
      <section aria-labelledby="consult-seen" className="av-card av-stack">
        <h2 id="consult-seen">{w("consultation.seen")}</h2>
        {c.visible.length === 0 ? <p className="av-muted">{w("consultation.seen_none")}</p> : <Entries items={c.visible} onChange={change} />}
        <details className="av-understand-later">
          <summary>{w("consultation.later", { count: c.later_count })}</summary>
          {c.later.length === 0 ? <p className="av-muted">{w("consultation.later_none")}</p> : <Entries items={c.later} onChange={change} />}
        </details>
      </section>
      {c.unknown.length > 0 && (
        <section aria-labelledby="consult-unknown" className="av-card av-stack">
          <h2 id="consult-unknown">{w("consultation.unknown")}</h2>
          <p className="av-muted">{w("consultation.unknown_hint")}</p>
          <Entries items={c.unknown} onChange={change} />
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
      <LaterNotes caseId={caseId} factId={factId} />
      </div>
      {c.entry.page_no != null && <PagePanel caseId={caseId} pageNo={c.entry.page_no} />}
      </div>
      <NoteEditor key={`${factId}-${c.note ?? ""}`} caseId={caseId} factId={factId} initial={c.note ?? ""} canAdd={can("consultation.note")} />
      <div>
        <BackLink to={`/cases/${caseId}/timeline`}>{w("consultation.back_bottom")}</BackLink>
      </div>
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
