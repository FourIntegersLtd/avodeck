// S29 Court orders and hearings (docs/design/screens/S29-CourtOrders.dc.html): the dates in the court's own days, a court
// order (it beats the usual dates, and can put the case on hold), hearings, and moving a date with the record of
// every change. Edits send the version the screen saw; a 412 asks which version to keep.
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { screenPath } from "../../foundation/lib/screens";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, Callout, Checkbox, Dialog, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import "./court-orders.css";
import { dateRange, keyName, longDate, Select, shortDate, useLocale, versionTag } from "./shared";
import { w } from "./words";

const ERRORS = { stale: "after_serving.error.stale", invalid: "after_serving.error.invalid" };
const REASONS = ["court_order", "agreement", "correction"] as const;
const HEARING_TYPES = ["trial", "case_management", "other"] as const;

/** The month of a date, written in the case's locale ("June"). */
function monthName(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: "long", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
}

/** A day and month without the year ("14 June"). */
function dayMonth(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
}

export function CourtOrders() {
  const { caseId = "" } = useParams();
  const locale = useLocale();
  const { item, pack, reload: reloadCase } = useCaseFrame();
  const path = { params: { path: { case_id: caseId } } };
  const deadlines = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deadlines", path)), [caseId]);
  const hearings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/hearings", path)), [caseId]);
  const directions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/court-directions", path)), [caseId]);
  const ids = (deadlines.data?.deadlines ?? []).map((d) => d.id);
  const history = useResource(
    async () => (await Promise.all(ids.map((id) => unwrap(api.GET("/v1/cases/{case_id}/deadlines/{deadline_id}/changes", { params: { path: { case_id: caseId, deadline_id: id } } }))))).flatMap((r, i) => r.changes.map((c) => ({ ...c, deadline: ids[i]! }))),
    [caseId, ids.join(",")],
  );
  const steps = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/after-serving/steps", path)), [caseId]);
  const [moving, setMoving] = useState<Schemas["DeadlineOut"] | null>(null);
  const [adding, setAdding] = useState<"order" | "hearing" | null>(null);

  if (deadlines.error || hearings.error || directions.error || steps.error) {
    return <div className="av-page"><LoadError onRetry={() => { deadlines.reload(); hearings.reload(); directions.reload(); steps.reload(); }} /></div>;
  }
  if (!deadlines.data || !hearings.data || !directions.data || !steps.data) return <div className="av-page"><Loading /></div>;
  const kindOf = (id: string) => deadlines.data!.deadlines.find((d) => d.id === id)?.kind ?? "";
  const refresh = () => { steps.reload(); deadlines.reload(); hearings.reload(); directions.reload(); history.reload(); };
  const trialPack = screenPath("trial_pack", { caseId });
  const trialMoved = !!steps.data.pack?.stale;
  const live = (h: Schemas["HearingOut"]) => !h.replaced_by_hearing_id && h.status !== "adjourned";
  const ordered = [...hearings.data.hearings].sort((a, b) => Number(live(b)) - Number(live(a)) || b.starts_on.localeCompare(a.starts_on));
  const sourceName = (s: string) => keyName("source", s);
  const movedTrial = hearings.data.hearings.find((h) => h.type === "trial" && live(h));
  // Where an adjourned hearing went and which order moved it, when both are known.
  const movedNote = (h: Schemas["HearingOut"]): string | null => {
    const next = hearings.data!.hearings.find((x) => x.id === h.replaced_by_hearing_id);
    const order = directions.data!.directions.find((d) => d.id === (next?.direction_id ?? h.direction_id));
    return next && order ? w("hearings.moved_to", { date: dayMonth(next.starts_on, locale), order: longDate(order.made_on, locale) }) : null;
  };

  return (
    <div className="av-page av-after-page av-court-orders av-stack">
      <div className="av-court-orders__head">
        <div className="av-court-orders__title">
          <BackLink to={`/cases/${caseId}/served`}>{w("back")}</BackLink>
          <h2>{w("orders.title")}</h2>
          <p className="av-muted av-after-lede">{w("orders.intro")}</p>
        </div>
        {adding !== "order" && <Button onClick={() => setAdding("order")}>{w("orders.add")}</Button>}
      </div>
      {trialMoved && (
        <Callout
          title={w("orders.trial_moved")}
          actions={trialPack ? <LinkButton variant="secondary" to={trialPack}>{w("orders.rebuild")}</LinkButton> : undefined}
        >
          <p>{movedTrial ? w("orders.trial_moved_month", { month: monthName(movedTrial.starts_on, locale) }) : w("orders.trial_moved_hint")}</p>
        </Callout>
      )}
      {directions.data.directions.some((d) => d.stay) && <Notice tone="warning"><p>{w("orders.stayed")}</p></Notice>}

      <section className="av-after-panel av-after-panel--roomy av-stack" aria-labelledby="hearings-title">
        <div className="av-court-orders__sechead">
          <h3 id="hearings-title">{w("hearings.title")}</h3>
          {adding !== "hearing" && <button type="button" className="av-after-linkbtn" onClick={() => setAdding("hearing")}>{w("hearings.add")}</button>}
        </div>
        {hearings.data.hearings.length === 0 && <p>{w("hearings.none")}</p>}
        <ul className="av-after-hearings">
          {ordered.map((h) => (
            <HearingRow key={`${h.id}-${h.version}`} caseId={caseId} hearing={h} locale={locale} moved={movedNote(h)} onDone={refresh} />
          ))}
        </ul>
        {adding === "hearing" && <HearingForm caseId={caseId} onDone={() => { setAdding(null); refresh(); }} />}
      </section>

      <section className="av-after-panel av-after-panel--roomy av-stack" aria-labelledby="orders-list">
        <h3 id="orders-list">{w("orders.list")}</h3>
        {directions.data.directions.length === 0 && <p>{w("orders.none")}</p>}
        <ul className="av-after-orders">
          {directions.data.directions.map((d) => {
            const set = d.created_deadline_ids
              .map((id) => deadlines.data!.deadlines.find((x) => x.id === id))
              .filter((x): x is Schemas["DeadlineOut"] => !!x)
              .map((x) => `${keyName("kind", x.kind)}, ${shortDate(x.due_on, locale)}`);
            return (
              <li key={d.id}>
                <article className="av-after-order av-stack" aria-labelledby={`order-${d.id}`}>
                  <div className="av-after-order__top">
                    <strong id={`order-${d.id}`}>{w("orders.made", { date: longDate(d.made_on, locale) })}</strong>
                    {d.reference && <span className="av-after-mono av-muted">{d.reference}</span>}
                  </div>
                  {d.text && <p className="av-after-words av-after-quote">{d.text}</p>}
                  {set.length > 0 && <p className="av-muted">{w("orders.dates_set", { dates: set.join("; ") })}</p>}
                </article>
              </li>
            );
          })}
        </ul>
        {adding === "order" ? (
          <OrderForm caseId={caseId} kinds={pack.deadline_rules.map((r) => String((r as { key: string }).key))} onDone={() => { setAdding(null); refresh(); }} />
        ) : (
          null
        )}
      </section>

      {(history.data ?? []).length > 0 && (
        <section className="av-after-panel av-after-panel--roomy av-stack" aria-labelledby="history-title">
          <h3 id="history-title">{w("deadlines.history")}</h3>
          <table className="av-after-dates" aria-labelledby="history-title">
            <colgroup><col style={{ width: "33.33%" }} /><col style={{ width: "17.42%" }} /><col style={{ width: "17.42%" }} /><col style={{ width: "31.83%" }} /></colgroup>
            <thead>
              <tr>
                <th scope="col">{w("deadlines.col_deadline")}</th>
                <th scope="col">{w("deadlines.col_was")}</th>
                <th scope="col">{w("deadlines.col_now")}</th>
                <th scope="col">{w("deadlines.col_why")}</th>
              </tr>
            </thead>
            <tbody>
              {history.data!.map((c) => (
                <tr key={c.id}>
                  <td><strong>{keyName("kind", kindOf(c.deadline))}</strong></td>
                  <td className="av-after-mono">{c.old_due_on ? <del>{shortDate(c.old_due_on, locale)}</del> : "–"}</td>
                  <td className="av-after-mono">{shortDate(c.new_due_on, locale)}</td>
                  <td>{keyName("deadlines.reason", c.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}


      <HoldOrWithdraw caseId={caseId} onHold={item.on_hold} onDone={() => { reloadCase(); refresh(); }} />
      <details className="av-after-disclosure">
        <summary id="dates-title">{w("deadlines.title")}</summary>
        <ul className="av-after-list">
          {deadlines.data.deadlines.map((d) => (
            <li key={d.id} className="av-after-item av-after-item--spread">
              <span>
                <strong>{keyName("kind", d.kind)}</strong>{" "}
                <span className="av-after-mono">{w("due", { date: longDate(d.due_on, locale) })}</span>{" "}
                <span className="av-badge">{sourceName(d.source)}</span>
              </span>
              <Button variant="secondary" onClick={() => setMoving(d)}>{w("deadlines.move")}</Button>
            </li>
          ))}
        </ul>
      </details>

      {moving && <MoveDialog caseId={caseId} deadline={moving} onClose={() => setMoving(null)} onDone={() => { setMoving(null); refresh(); }} />}
    </div>
  );
}

const HOLD_ERRORS = { stale: "after_serving.error.stale", invalid: "after_serving.error.invalid" };

function HoldOrWithdraw({ caseId, onHold, onDone }: { caseId: string; onHold: boolean; onDone: () => void }) {
  const stepUp = useStepUp();
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const [withdrawn, setWithdrawn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = { params: { path: { case_id: caseId } } };
  const toggle = async () => {
    setError(null);
    try {
      await unwrap(onHold ? api.POST("/v1/cases/{case_id}/resume", path) : api.POST("/v1/cases/{case_id}/hold", path));
      onDone();
    } catch (err) {
      setError(errorMessage(err, HOLD_ERRORS));
    }
  };
  const withdraw = async () => {
    setError(null);
    try {
      const out = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/withdraw", { ...path, body: { reason } })));
      if (out) {
        setAsking(false);
        setWithdrawn(true);
        onDone();
      }
    } catch (err) {
      setError(errorMessage(err, HOLD_ERRORS));
    }
  };
  return (
    <section className="av-after-hold" aria-labelledby="hold-title">
      <div className="av-after-hold__text">
        <h3 id="hold-title">{w("hold.title")}</h3>
        <p className="av-muted">{w("hold.hint")}</p>
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        {withdrawn && <Notice tone="success"><p>{w("hold.withdrawn")}</p></Notice>}
      </div>
      {!withdrawn && (
        <>
          <Button variant="secondary" onClick={() => void toggle()}>{onHold ? w("hold.resume") : w("hold.put")}</Button>
          <Button variant="secondary" className="av-after-amberbtn" onClick={() => setAsking(true)}>{w("hold.withdraw")}</Button>
        </>
      )}
      {asking && (
        <Dialog title={w("hold.withdraw")} onClose={() => setAsking(false)}>
          <form className="av-stack" onSubmit={(e: FormEvent) => { e.preventDefault(); void withdraw(); }}>
            <p>{w("hold.withdraw_hint")}</p>
            <TextField label={w("hold.reason")} name="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="av-row">
              <Button type="submit" disabled={!reason.trim()}>{w("hold.confirm")}</Button>
              <Button variant="secondary" onClick={() => setAsking(false)}>{w("cancel")}</Button>
            </div>
          </form>
        </Dialog>
      )}
    </section>
  );
}

function MoveDialog({ caseId, deadline, onClose, onDone }: { caseId: string; deadline: Schemas["DeadlineOut"]; onClose: () => void; onDone: () => void }) {
  const [dueOn, setDueOn] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const send = async (version: number) => {
    setError(null);
    try {
      await unwrap(api.PATCH("/v1/cases/{case_id}/deadlines/{deadline_id}", {
        params: { path: { case_id: caseId, deadline_id: deadline.id } },
        body: { due_on: dueOn, ...(reason ? { reason } : {}) },
        headers: ifMatch(versionTag(version)),
      }));
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 412) setStale(true);
      else setError(errorMessage(err, ERRORS));
    }
  };
  const keepMine = async () => {
    setStale(false);
    try {
      const fresh = await unwrap(api.GET("/v1/cases/{case_id}/deadlines", { params: { path: { case_id: caseId } } }));
      await send(fresh.deadlines.find((d) => d.id === deadline.id)?.version ?? deadline.version);
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };

  if (stale) {
    return (
      <Dialog title={w("stale_title")} onClose={onClose}>
        <div className="av-stack">
          <Button onClick={() => void keepMine()}>{w("stale_mine")}</Button>
          <Button variant="secondary" onClick={onDone}>{w("stale_theirs")}</Button>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title={w("deadlines.move_title")} onClose={onClose}>
      <form className="av-stack" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(deadline.version); }}>
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <TextField label={w("deadlines.new_date")} name="due_on" type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
        <Select label={w("deadlines.reason")} value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="" />
          {REASONS.map((r) => <option key={r} value={r}>{w(`deadlines.reason.${r}`)}</option>)}
        </Select>
        <div className="av-row">
          <Button type="submit" disabled={!dueOn}>{w("deadlines.move_submit")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function OrderForm({ caseId, kinds, onDone }: { caseId: string; kinds: string[]; onDone: () => void }) {
  const [madeOn, setMadeOn] = useState("");
  const [reference, setReference] = useState("");
  const [text, setText] = useState("");
  const [kind, setKind] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [stay, setStay] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/court-directions", {
        params: { path: { case_id: caseId } },
        body: { made_on: madeOn, ...(reference ? { reference } : {}), ...(text ? { text } : {}), stay, deadlines: kind && dueOn ? [{ kind, due_on: dueOn }] : [] },
      }));
      onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <form className="av-stack" onSubmit={(e) => void submit(e)}>
      <h4>{w("orders.add")}</h4>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("orders.made_on")} name="made_on" type="date" value={madeOn} onChange={(e) => setMadeOn(e.target.value)} />
      <TextField label={w("orders.reference")} name="reference" value={reference} onChange={(e) => setReference(e.target.value)} />
      <TextField label={w("orders.text")} name="text" value={text} onChange={(e) => setText(e.target.value)} />
      <Select label={w("orders.deadline_kind")} value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="">{w("orders.deadline_none")}</option>
        {kinds.map((k) => <option key={k} value={k}>{keyName("kind", k)}</option>)}
      </Select>
      {kind && <TextField label={w("orders.deadline_due")} name="due_on" type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />}
      <Checkbox label={w("orders.stay")} checked={stay} onChange={(e) => setStay(e.target.checked)} />
      <div className="av-after-footer"><Button type="submit" disabled={!madeOn}>{w("orders.add_submit")}</Button></div>
    </form>
  );
}

function HearingForm({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const [type, setType] = useState<string>(HEARING_TYPES[0]);
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [venue, setVenue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/hearings", { params: { path: { case_id: caseId } }, body: { type, starts_on: startsOn, ends_on: endsOn, ...(venue ? { venue } : {}) } }));
      onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <form className="av-stack" onSubmit={(e) => void submit(e)}>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <Select label={w("hearings.type")} value={type} onChange={(e) => setType(e.target.value)}>
        {HEARING_TYPES.map((h) => <option key={h} value={h}>{w(`hearings.type.${h}`)}</option>)}
      </Select>
      <TextField label={w("hearings.starts_on")} name="starts_on" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
      <TextField label={w("hearings.ends_on")} name="ends_on" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
      <TextField label={w("hearings.venue")} name="venue" value={venue} onChange={(e) => setVenue(e.target.value)} />
      <div className="av-after-footer"><Button type="submit" disabled={!startsOn || !endsOn}>{w("hearings.add_submit")}</Button></div>
    </form>
  );
}

function HearingRow({ caseId, hearing, locale, moved, onDone }: { caseId: string; hearing: Schemas["HearingOut"]; locale: string; moved: string | null; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const adjourn = async () => {
    try {
      await unwrap(api.PATCH("/v1/cases/{case_id}/hearings/{hearing_id}", { params: { path: { case_id: caseId, hearing_id: hearing.id } }, body: { status: "adjourned" }, headers: ifMatch(versionTag(hearing.version)) }));
      onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const known = ["scheduled", "heard", "vacated", "adjourned"].includes(hearing.status);
  const [changing, setChanging] = useState(false);
  const days = Math.round((Date.parse(hearing.ends_on) - Date.parse(hearing.starts_on)) / 86400000) + 1;
  const where = [hearing.venue, w("hearings.days", { count: days })].filter(Boolean).join(" · ");
  const live = !hearing.replaced_by_hearing_id && hearing.status !== "adjourned";
  return (
    <li className={`av-after-hearing${live ? "" : " av-after-hearing--past"}`} aria-label={keyName("hearings.type", hearing.type)}>
      <span className="av-after-mono av-after-hearing__date">{dateRange(hearing.starts_on, hearing.ends_on, locale)}</span>
      <span className="av-after-hearing__what">
        <strong>{keyName("hearings.type", hearing.type)}</strong>
        <span className="av-muted">{moved ?? where}</span>
      </span>
      <span className={`av-after-state${hearing.status === "scheduled" ? " av-after-state--on" : ""}`}>{known ? w(`hearings.status.${hearing.status as "scheduled"}`) : hearing.status}</span>
      {hearing.status === "scheduled" && !changing && <button type="button" className="av-after-linkbtn" onClick={() => setChanging(true)}>{w("hearings.change")}</button>}
      {changing && <Button variant="secondary" onClick={() => void adjourn()}>{w("hearings.adjourn")}</Button>}
      {error && <span role="alert">{error}</span>}
    </li>
  );
}
