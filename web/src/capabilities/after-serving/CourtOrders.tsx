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
import { dateRange, keyName, longDate, Select, shortDate, useLocale, versionTag } from "./shared";
import { w } from "./words";

const ERRORS = { stale: "after_serving.error.stale", invalid: "after_serving.error.invalid" };
const REASONS = ["court_order", "agreement", "correction"] as const;
const HEARING_TYPES = ["trial", "case_management", "other"] as const;

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

  return (
    <div className="av-page av-stack">
      <BackLink to={`/cases/${caseId}/served`}>{w("back")}</BackLink>
      <h2>{w("orders.title")}</h2>
      <p>{w("orders.intro")}</p>
      {trialMoved && (
        <Callout
          title={w("orders.trial_moved")}
          actions={trialPack ? <LinkButton to={trialPack}>{w("orders.rebuild")}</LinkButton> : undefined}
        >
          <p>{w("orders.trial_moved_hint")}</p>
        </Callout>
      )}
      {directions.data.directions.some((d) => d.stay) && <Notice tone="warning"><p>{w("orders.stayed")}</p></Notice>}

      <section className="av-card av-stack" aria-labelledby="dates-title">
        <h3 id="dates-title">{w("deadlines.title")}</h3>
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
      </section>

      <section className="av-card av-stack" aria-labelledby="hearings-title">
        <h3 id="hearings-title">{w("hearings.title")}</h3>
        {hearings.data.hearings.length === 0 && <p>{w("hearings.none")}</p>}
        <ul className="av-after-list">
          {ordered.map((h) => (
            <HearingRow key={`${h.id}-${h.version}`} caseId={caseId} hearing={h} locale={locale} onDone={refresh} />
          ))}
        </ul>
        {adding === "hearing" ? <HearingForm caseId={caseId} onDone={() => { setAdding(null); refresh(); }} /> : <Button variant="secondary" onClick={() => setAdding("hearing")}>{w("hearings.add")}</Button>}
      </section>

      <section className="av-card av-stack" aria-labelledby="orders-list">
        <h3 id="orders-list">{w("orders.list")}</h3>
        {directions.data.directions.length === 0 && <p>{w("orders.none")}</p>}
        <ul className="av-after-list">
          {directions.data.directions.map((d) => {
            const set = d.created_deadline_ids
              .map((id) => deadlines.data!.deadlines.find((x) => x.id === id))
              .filter((x): x is Schemas["DeadlineOut"] => !!x)
              .map((x) => `${keyName("kind", x.kind)}, ${shortDate(x.due_on, locale)}`);
            return (
              <li key={d.id}>
                <article className="av-after-item av-stack" aria-labelledby={`order-${d.id}`}>
                  <strong id={`order-${d.id}`}>{w("orders.made", { date: longDate(d.made_on, locale) })}</strong>
                  {d.reference && <span className="av-after-mono">{d.reference}</span>}
                  {d.text && <p className="av-after-words">{d.text}</p>}
                  {set.length > 0 && <p className="av-muted">{w("orders.dates_set", { dates: set.join("; ") })}</p>}
                </article>
              </li>
            );
          })}
        </ul>
        {adding === "order" ? (
          <OrderForm caseId={caseId} kinds={pack.deadline_rules.map((r) => String((r as { key: string }).key))} onDone={() => { setAdding(null); refresh(); }} />
        ) : (
          <div className="av-after-footer"><Button onClick={() => setAdding("order")}>{w("orders.add")}</Button></div>
        )}
      </section>

      {(history.data ?? []).length > 0 && (
        <section className="av-card av-stack" aria-labelledby="history-title">
          <h3 id="history-title">{w("deadlines.history")}</h3>
          <table className="av-after-table" aria-labelledby="history-title">
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
    <section className="av-card av-stack" aria-labelledby="hold-title">
      <h3 id="hold-title">{w("hold.title")}</h3>
      <p>{w("hold.hint")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {withdrawn && <Notice tone="success"><p>{w("hold.withdrawn")}</p></Notice>}
      {!withdrawn && (
        <div className="av-row">
          <Button variant="secondary" onClick={() => void toggle()}>{onHold ? w("hold.resume") : w("hold.put")}</Button>
          <Button variant="secondary" onClick={() => setAsking(true)}>{w("hold.withdraw")}</Button>
        </div>
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

function HearingRow({ caseId, hearing, locale, onDone }: { caseId: string; hearing: Schemas["HearingOut"]; locale: string; onDone: () => void }) {
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
  return (
    <li className="av-after-item av-after-item--spread" aria-label={keyName("hearings.type", hearing.type)}>
      <span className="av-stack">
        <strong className="av-after-mono">{dateRange(hearing.starts_on, hearing.ends_on, locale)}</strong>
        <span>{keyName("hearings.type", hearing.type)}</span>
        <span className="av-muted">{where}</span>
      </span>
      <span className="av-row">
        <span className="av-badge">{known ? w(`hearings.status.${hearing.status as "scheduled"}`) : hearing.status}</span>
        {hearing.status === "scheduled" && !changing && <Button variant="secondary" onClick={() => setChanging(true)}>{w("hearings.change")}</Button>}
        {changing && <Button variant="secondary" onClick={() => void adjourn()}>{w("hearings.adjourn")}</Button>}
      </span>
      {error && <span role="alert">{error}</span>}
    </li>
  );
}
