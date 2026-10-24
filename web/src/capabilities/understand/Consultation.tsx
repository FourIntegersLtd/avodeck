// S12 One consultation, as the clinician saw it. The title (with the clinician's role) and the guidance in force
// come from the API (both from the case's packs); later entries are counted and folded away; entries with no
// known arrival are shown as not known. Any day can be chosen. Each person keeps a private note, and the named
// expert may change when something arrived, with a reason. Buttons follow what the person may do on the case.
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, DictationBox, Dialog, LinkButton, LoadError, Loading, Notice, PageRef, QChip, StatusBadge, ToggleSwitch } from "../../foundation/ui";
import { Comments } from "./Comments";
import { GuidanceChanged, longDate, statusOf, TextArea, useCan, useKindWord } from "./shared";
import { w } from "./words";
import { groupByPage, splitByVisitPage } from "./consultationEntries";
import { pageImage } from "./pageImage";

type Entry = Schemas["Entry"];
type PageMark = Schemas["PageMark"];

const pct = (share: number) => `${Math.round(share * 10000) / 100}%`;
const placed = (m: PageMark): React.CSSProperties => ({ left: pct(m.box.x0), top: pct(m.box.y0), width: pct(m.box.x1 - m.box.x0), height: pct(m.box.y1 - m.box.y0) });

type EntryVariant = "known" | "unknown";

/** A dot for a fact's state: filled when you checked it, dashed when it still needs checking, amber for a thing to look at. */
function StateDot({ entry }: { entry: Entry }) {
  const state = entry.has_finding ? "look" : statusOf(entry.status) === "checked" ? "checked" : "please_check";
  return <span className="av-understand-key" data-state={state} aria-hidden="true" />;
}

function EntryRow({ entry, variant, onChange }: { entry: Entry; variant: EntryVariant; onChange?: (e: Entry) => void }) {
  const word = useKindWord();
  return (
    <li className="av-understand-entry av-understand-entry--compact">
      <StateDot entry={entry} />
      <div className="av-understand-entry__line">
        <span>
          <strong>
            {word(entry.kind)}, {longDate(entry.event_date)}.
          </strong>{" "}
          <span className="av-understand-entry__summary">{entry.summary}</span>
        </span>
        <div className="av-understand-entry__meta">
          {variant === "known" && entry.available_at && <span className="av-muted">{w("consultation.arrived", { date: longDate(entry.available_at) })}</span>}
          <span className="av-visually-hidden">{statusOf(entry.status) === "checked" ? t("ui.status.checked") : t("ui.status.please_check")}</span>
          {entry.has_finding && <StatusBadge status="look" />}
          {onChange && (variant === "unknown" || !entry.available_at) && (
            <button type="button" className="av-understand-entry__set-arrival" onClick={() => onChange(entry)}>
              {w("consultation.set_arrival")}
            </button>
          )}
        </div>
      </div>
      <PageRef page={entry.page_no} />
    </li>
  );
}

function EntriesList({ items, variant, onChange }: { items: Entry[]; variant: EntryVariant; onChange?: (e: Entry) => void }) {
  return (
    <ul className="av-understand-list av-understand-entries">
      {items.map((e) => (
        <EntryRow key={e.fact_id} entry={e} variant={variant} onChange={onChange} />
      ))}
    </ul>
  );
}

function PageGroups({ items, variant, onChange }: { items: Entry[]; variant: EntryVariant; onChange?: (e: Entry) => void }) {
  const groups = groupByPage(items);
  return (
    <>
      {groups.map((g) => (
        <div key={g.page} className="av-understand-entries__group">
          <h3>{w("consultation.page_group", { page: g.page })}</h3>
          <EntriesList items={g.items} variant={variant} onChange={onChange} />
        </div>
      ))}
    </>
  );
}

function GroupedEntries({ items, visitPage, variant, onChange }: { items: Entry[]; visitPage: number | null | undefined; variant: EntryVariant; onChange?: (e: Entry) => void }) {
  const { onPage, otherPages } = splitByVisitPage(items, visitPage);
  const foldOther = otherPages.length > 2;
  return (
    <div className="av-stack">
      {onPage.length > 0 && (
        <div className="av-understand-entries__group">
          <h3>{w("consultation.on_this_page")}</h3>
          <EntriesList items={onPage} variant={variant} onChange={onChange} />
        </div>
      )}
      {otherPages.length > 0 &&
        (foldOther ? (
          <details className="av-understand-later">
            <summary>{w("consultation.from_other_pages", { count: otherPages.length })}</summary>
            <PageGroups items={otherPages} variant={variant} onChange={onChange} />
          </details>
        ) : (
          <PageGroups items={otherPages} variant={variant} onChange={onChange} />
        ))}
    </div>
  );
}

function SeenEntries({ items, visitPage, onChange }: { items: Entry[]; visitPage: number | null | undefined; onChange?: (e: Entry) => void }) {
  const body = <GroupedEntries items={items} visitPage={visitPage} variant="known" onChange={onChange} />;
  if (items.length > 8) {
    return (
      <details>
        <summary>{w("consultation.seen_fold", { count: items.length })}</summary>
        {body}
      </details>
    );
  }
  return body;
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

/** The note card and the foot of the screen (the way back, and save and carry on to the things to look at). */
function NoteEditor({ caseId, factId, initial, canAdd }: { caseId: string; factId: string; initial: string; canAdd: boolean }) {
  const [body, setBody] = useState(initial);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const writable = canAdd || initial !== "";
  const save = async (thenLeave: boolean) => {
    setError(null);
    setSaved(false);
    try {
      if (writable) await unwrap(api.PUT("/v1/cases/{case_id}/consultations/{fact_id}/note", { params: { path: { case_id: caseId, fact_id: factId } }, body: { body } }));
      setSaved(true);
      if (thenLeave) navigate(`/cases/${caseId}/things-to-look-at`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <>
      <section aria-labelledby="consult-note" className="av-card av-stack av-understand-area-note">
        <h2 id="consult-note">{w("consultation.note_title")}</h2>
        <p className="av-muted" id="consult-note-hint">{w("consultation.note_hint")}</p>
        {writable && <DictationBox caseId={caseId} context="consultation_note" label={w("consultation.note")} rows={4} bar value={body} onChange={(v) => { setBody(v); setSaved(false); }} />}
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        {saved && <Notice tone="success"><p>{w("consultation.note_saved")}</p></Notice>}
        {writable && (
          <div className="av-row">
            <Button variant="quiet" onClick={() => void save(false)}>{w("consultation.note_save")}</Button>
          </div>
        )}
      </section>
      <div className="av-understand-foot av-understand-area-foot">
        <LinkButton to={`/cases/${caseId}/timeline`} variant="secondary">
          {w("consultation.back_bottom")}
        </LinkButton>
        <Button arrow onClick={() => void save(true)}>{w("consultation.note_continue")}</Button>
      </div>
    </>
  );
}

function PagePanel({ caseId, pageNo, marks }: { caseId: string; pageNo: number; marks: PageMark[] }) {
  const shown = useResource(() => pageImage(caseId, pageNo), [caseId, pageNo]);
  return (
    <section aria-labelledby="consult-page" className="av-card av-stack av-understand-page av-understand-area-page">
      <div className="av-understand-page__head">
        <h2 id="consult-page">{w("consultation.page_title")}</h2>
        <PageRef page={pageNo} />
      </div>
      <p className="av-muted av-understand-page__context">{w("consultation.page_context", { page: pageNo })}</p>
      {shown.error ? (
        <p className="av-muted">{w("consultation.page_none")}</p>
      ) : shown.data ? (
        <div className="av-understand-page__sheet">
          <img src={shown.data} alt={w("consultation.page_alt", { page: pageNo })} />
          {marks.map((m) => (
            <span key={`${m.fact_id}-${m.role}`} className={`av-understand-mark av-understand-mark--${m.role}`} data-testid={`page-mark-${m.role}`} style={placed(m)} aria-hidden="true" />
          ))}
        </div>
      ) : (
        <p className="av-muted">{w("consultation.page_loading")}</p>
      )}
      {marks.some((m) => m.role === "later") && <p className="av-muted">{w("consultation.marks_note")}</p>}
    </section>
  );
}

function LaterNotes({ caseId, factId }: { caseId: string; factId: string }) {
  const word = useKindWord();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/consultations/{fact_id}/later-notes", { params: { path: { case_id: caseId, fact_id: factId } } })), [caseId, factId], { cache: "understand/Consultation#LaterNotes:found" });
  return (
    <section aria-labelledby="consult-later-notes" className="av-card av-stack av-understand-card">
      <h2 id="consult-later-notes">{w("consultation.later_notes_title")}</h2>
      <p className="av-muted">{w("consultation.later_notes_hint")}</p>
      {found.error ? (
        <LoadError onRetry={found.reload} />
      ) : !found.data ? (
        <Loading inline />
      ) : found.data.notes.length === 0 ? (
        <p className="av-muted">{w("consultation.later_notes_none")}</p>
      ) : (
        <ul className="av-understand-list av-understand-entries">
          {found.data.notes.map((n) => (
            <li key={n.fact_id} className="av-understand-entry av-understand-entry--plain">
              <span className="av-understand-entry__words">
                <span>
                  {longDate(n.event_date)} · {word(n.kind)}
                </span>
                {n.has_note && <span className="av-muted">{w("consultation.later_notes_mine")}</span>}
              </span>
              <PageRef page={n.page_no} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Consultation() {
  const { caseId = "", factId = "" } = useParams();
  const word = useKindWord();
  const [changing, setChanging] = useState<Entry | null>(null);
  const [on, setOn] = useState<string | null>(null);
  const [typed, setTyped] = useState<string | null>(null);
  const [asSeen, setAsSeen] = useState(true);
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
  if (found.error) {
    const err = found.error;
    if (err instanceof ApiError && err.code === "undated_entry") {
      const page = typeof err.extra.page_no === "number" ? err.extra.page_no : null;
      const readTo = page ? `/cases/${caseId}/read/${page}?fact=${factId}` : `/cases/${caseId}/read`;
      return (
        <div className="av-page av-stack">
          <BackLink to={`/cases/${caseId}/timeline`}>{w("common.back_to_timeline")}</BackLink>
          <Notice>
            <p>{w("consultation.undated_entry")}</p>
            <LinkButton to={readTo}>{w("consultation.undated_entry_read")}</LinkButton>
          </Notice>
        </div>
      );
    }
    return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  }
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const c = found.data;
  const day = c.entry.event_date ?? c.entry.available_at;
  const change = can("availability.override") ? setChanging : undefined;
  const pick = (value: string) => {
    setTyped(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) setOn(value);
  };
  return (
    <div className="av-page av-page--wide av-stack">
      <div className="av-understand-top">
        <div>
          <BackLink to={`/cases/${caseId}/timeline`}>{w("common.back_to_timeline")}</BackLink>
          <h2>{w("consultation.heading", { kind: word(c.entry.kind), date: longDate(day) })}</h2>
          {c.entry.author_line && <p className="av-understand-author">{c.entry.author_line}</p>}
          {c.question_numbers.length > 0 ? (
            <div role="group" aria-label={w("consultation.linked_to")} className="av-understand-chips av-understand-linked">
              <span className="av-muted">{w("consultation.linked_to")}</span>
              {c.question_numbers.map((n) => (
                <QChip key={n} n={n} />
              ))}
            </div>
          ) : (
            <p className="av-muted">{w("timeline.not_linked")}</p>
          )}
        </div>
        <div className="av-understand-asseen">
          <ToggleSwitch label={w("consultation.on", { title: c.title, date: longDate(c.on) })} checked={asSeen} onChange={setAsSeen} />
          <span className="av-understand-asseen__hint">{w("consultation.switch_hint")}</span>
          <details className="av-understand-asseen__day">
            <summary>{w("consultation.change_day")}</summary>
            <div className="av-field">
              <label className="av-field__label" htmlFor="consult-on">{w("consultation.on_label")}</label>
              <input id="consult-on" className="av-input" type="date" value={typed ?? c.on} onChange={(e) => pick(e.target.value)} />
            </div>
          </details>
        </div>
      </div>
      <GuidanceChanged codes={c.guidance_changed} />
      <div className="av-understand-consult">
        {c.entry.page_no != null && <PagePanel caseId={caseId} pageNo={c.entry.page_no} marks={c.page_marks ?? []} />}
        <div className="av-stack av-understand-area-aside">
          <section aria-labelledby="consult-seen" className="av-card av-stack av-understand-card">
            <h2 id="consult-seen">{c.seen_title ?? w("consultation.seen")}</h2>
            {c.visible.length === 0 ? <p className="av-muted">{w("consultation.seen_none")}</p> : <SeenEntries items={c.visible} visitPage={c.entry.page_no} onChange={change} />}
            <details className="av-understand-later" open={!asSeen || undefined}>
              <summary>{w("consultation.later", { count: c.later_count })}</summary>
              {c.later.length === 0 ? (
                <p className="av-muted">{w("consultation.later_none")}</p>
              ) : (
                <GroupedEntries items={c.later} visitPage={c.entry.page_no} variant="known" onChange={change} />
              )}
            </details>
          </section>
          {c.unknown.length > 0 && (
            <section aria-labelledby="consult-unknown" className="av-card av-stack av-understand-card av-understand-card--soft">
              <h2 id="consult-unknown">{c.unseen_title ?? w("consultation.unknown")}</h2>
              <p className="av-muted">{w("consultation.unknown_hint")}</p>
              {c.unknown.length > 3 ? (
                <details>
                  <summary>{w("consultation.unknown_fold", { count: c.unknown.length })}</summary>
                  <GroupedEntries items={c.unknown} visitPage={c.entry.page_no} variant="unknown" onChange={change} />
                </details>
              ) : (
                <GroupedEntries items={c.unknown} visitPage={c.entry.page_no} variant="unknown" onChange={change} />
              )}
            </section>
          )}
          <section aria-labelledby="consult-guidance" className="av-card av-stack av-understand-card">
            <h2 id="consult-guidance">{w("consultation.guidance")}</h2>
            {c.guidance.length === 0 && <p className="av-muted">{w("consultation.guidance_none")}</p>}
            <ul className="av-understand-list">
              {c.guidance.map((g) => {
                const name = `${g.title ?? g.code} (${g.code})`;
                return (
                  <li key={`${g.code}-${g.version_label}`} className="av-understand-guidance">
                    {g.url ? <a href={g.url} target="_blank" rel="noopener noreferrer">{name}</a> : <strong>{name}</strong>}
                    <span className="av-muted">{g.version_label}</span>
                  </li>
                );
              })}
            </ul>
            {c.guidance.length > 0 && (
              <>
                <span className="av-understand-count av-understand-inforce">{w("consultation.in_force", { date: longDate(c.on) })}</span>
                <p className="av-muted">{w("consultation.guidance_hint")}</p>
              </>
            )}
          </section>
          <LaterNotes caseId={caseId} factId={factId} />
          <details className="av-understand-comments">
            <summary>{w("comments.title")}</summary>
            <Comments caseId={caseId} targetKind="fact" targetId={factId} />
          </details>
        </div>
        <NoteEditor key={`${factId}-${c.note ?? ""}`} caseId={caseId} factId={factId} initial={c.note ?? ""} canAdd={can("consultation.note")} />
      </div>
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
