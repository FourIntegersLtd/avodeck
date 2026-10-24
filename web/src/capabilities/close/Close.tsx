// S26 Close the case (docs/design/screens/S26-Close.dc.html): keep your copies, how long we hold
// the records, proof of deletion (with pausing), and closing. The server decides every step;
// this screen shows what `allowed_actions` and the close summary allow.
import { useEffect, useId, useState, type ReactNode } from "react";
import { useContext } from "react";
import { useParams } from "react-router-dom";
import { caseSubtitle, stepFor } from "../../foundation/lib/caseFrame";
import { SessionContext } from "../../foundation/lib/session";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, CaseHeader, Dialog, LoadError, Loading, Notice, TextField, useBusy } from "../../foundation/ui";
import "./close.css";
import { w, type CloseKey } from "./words";

type Summary = Schemas["CloseSummary"];
type Policy = "delete_now" | "keep_default" | "return_then_delete";
type Message = { tone: "success" | "error"; text: string } | null;

const OWN_ERRORS = { conflict: "close.error.conflict", forbidden: "close.error.forbidden" };
const HOLD_REASONS = ["appeal", "related_claim", "complaint", "other"] as const;
const POLL_MS = 800;
const HEADINGS = new Set(["active", "served", "settled", "archived", "withdrawn", "deleted"]);

/** A calendar date or moment with its year, in the case's locale. */
function formatDate(iso: string, locale: string | undefined): string {
  const date = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso);
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" };
  try {
    return new Intl.DateTimeFormat(locale, options).format(date);
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
}

export function Close() {
  const { caseId = "" } = useParams();
  const path = { case_id: caseId };
  const item = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", { params: { path } })), [caseId], { cache: "close/Close#Close:item" });
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path } })), [caseId], { cache: "close/Close#Close:pack" });
  const close = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/close", { params: { path } })), [caseId], { cache: "close/Close#Close:close" });
  const [message, setMessage] = useState<Message>(null);
  const me = useContext(SessionContext)?.me;
  const person = me && (me.person.full_name || me.person.email);

  const error = item.error ?? pack.error ?? close.error;
  // A deleted case answers only its certificate (the rest is 404), so show the proof on its own.
  if (item.error instanceof ApiError && item.error.status === 404) return <DeletedCase caseId={caseId} />;
  if (error) return <div className="av-page"><LoadError onRetry={() => { item.reload(); pack.reload(); close.reload(); }} /></div>;
  if (!item.data || !pack.data || !close.data) return <div className="av-page"><Loading /></div>;

  const actions = new Set(item.data.allowed_actions);
  const can = (a: string) => actions.has(a);
  const summary = close.data;
  const locale = pack.data.locale;
  const deleted = summary.status === "deleted" || summary.deleted_at !== null;
  const status = deleted ? "deleted" : summary.status;
  const headingKey = (HEADINGS.has(status) ? `heading.${status}` : "heading.active") as CloseKey;
  const refresh = () => {
    item.reload();
    close.reload();
  };
  const steps = pack.data.workflow_steps as Array<{ key: string; screens?: string[] }>;

  let intro: string | null = w("intro.open");
  if (deleted) intro = null;
  else if (summary.deletion_started) intro = w("intro.deleting");
  else if (summary.settled_at) {
    const date = formatDate(summary.settled_at, locale);
    const party = partyWord(pack.data, item.data.instructing_party_type);
    intro = party ? w("intro.settled_by", { party, date }) : w("intro.settled", { date });
  }

  return (
    <>
    <CaseHeader
      bar
      caseId={caseId}
      steps={steps}
      current={stepFor(pack.data, "close")}
      title={<span className="av-case-bar__title">{item.data.label}</span>}
      subtitle={[caseSubtitle(item.data, pack.data), w(`status.${status}` as CloseKey)].filter(Boolean).join(" · ")}
      person={person}
    />
    <div className="av-page av-stack av-close">
      <div className="av-close-title">
        {!deleted && (
          <span className="av-close-title__mark" aria-hidden="true">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          </span>
        )}
        <div>
          <h1>{w(headingKey)}</h1>
          {intro && <p className="av-close-title__lead">{intro}</p>}
        </div>
      </div>
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      {deleted ? (
        can("case.view_certificate") && <Certificate caseId={caseId} locale={locale} />
      ) : (
        <>
          <div className="av-close-grid">
          {can("case.export") && <Copies caseId={caseId} adminCopy={!can("case.settle")} />}
          {can("case.retention") && !summary.deletion_started && (
            <Keep
              caseId={caseId}
              summary={summary}
              canDeleteNow={can("case.delete_now")}
              party={partyWord(pack.data, item.data.instructing_party_type)}
              onSaved={close.reload}
            />
          )}
          <Proof caseLabel={item.data.label} party={partyWord(pack.data, item.data.instructing_party_type)} summary={summary} locale={locale} />
          </div>
          <Hold caseId={caseId} summary={summary} locale={locale} canHold={can("legal_hold.set")} canRelease={can("legal_hold.release")} onChanged={close.reload} />
          {!summary.deletion_started && <Closing party={partyWord(pack.data, item.data.instructing_party_type)} caseId={caseId} status={summary.status} can={can} onDone={refresh} setMessage={setMessage} />}
        </>
      )}
    </div>
    </>
  );
}

function partyWord(pack: Schemas["CasePack"], key: string | null | undefined): string | null {
  if (!key) return null;
  return pack.instructing_party_types.find((p) => p.key === key)?.label ?? null;
}

function Section({ title, step, children }: { title: string; step?: number; children: ReactNode }) {
  const id = useId();
  return (
    <section className="av-card av-stack av-close-card" aria-labelledby={id}>
      <div className="av-close-card__head">
        {step !== undefined && <span className="av-close-card__step">{step}</span>}
        <h2 id={id}>{title}</h2>
      </div>
      {children}
    </section>
  );
}

type Making = { state: "idle" } | { state: "making"; job: string } | { state: "ready"; job: string } | { state: "done" } | { state: "failed" };

function Copies({ caseId, adminCopy }: { caseId: string; adminCopy: boolean }) {
  const stepUp = useStepUp();
  const [making, setMaking] = useState<Making>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const kinds = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/export/files", { params: { path: { case_id: caseId } } })), [caseId], { cache: "close/Close#Copies:kinds" });
  // Ticks the user has changed; anything else follows the server's defaults.
  const [changed, setChanged] = useState<Record<string, boolean>>({});
  const files = kinds.data?.files ?? [];
  const isOn = (f: { kind: string; selected: boolean }) => changed[f.kind] ?? f.selected;
  const chosen = files.filter(isOn).map((f) => f.kind);

  useEffect(() => {
    if (making.state !== "making") return;
    let live = true;
    const timer = setInterval(() => {
      void unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: making.job } } })).then(
        (job) => {
          if (!live) return;
          if (job.status === "done") setMaking({ state: "ready", job: making.job });
          else if (job.status === "failed" || job.status === "cancelled") setMaking({ state: "failed" });
        },
        () => live && setMaking({ state: "failed" }),
      );
    }, POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [making]);

  const make = async () => {
    setError(null);
    try {
      const started = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/export", { params: { path: { case_id: caseId } }, body: { files: chosen } })));
      if (started) setMaking({ state: "making", job: started.job_id });
    } catch (err) {
      setError(errorMessage(err, OWN_ERRORS));
    }
  };

  const download = async (job: string) => {
    setError(null);
    try {
      const blob = await stepUp(() =>
        unwrap(api.GET("/v1/cases/{case_id}/export/{job_id}/download", { params: { path: { case_id: caseId, job_id: job } }, parseAs: "blob" })),
      );
      if (!blob) return;
      if (typeof URL.createObjectURL === "function") {
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob as Blob);
        link.download = `case-${caseId.slice(0, 8)}.zip`;
        link.click();
        URL.revokeObjectURL(link.href);
      }
      setMaking({ state: "done" });
    } catch (err) {
      setMaking({ state: "idle" });
      setError(errorMessage(err, { ...OWN_ERRORS, not_found: "close.copies.gone" }));
    }
  };

  return (
    <Section title={w("copies.heading")} step={1}>
      <p>{adminCopy ? w("copies.admin_lead") : w("copies.lead")}</p>
      {kinds.error ? (
        <LoadError onRetry={kinds.reload} />
      ) : (
        <div className="av-close-ticks" role="group" aria-label={w("copies.heading")}>
          {files.map((f) => (
            <CopyKind
              key={f.kind}
              label={w(`copies.${f.kind}`)}
              count={f.count}
              checked={isOn(f)}
              disabled={making.state === "making" || making.state === "ready"}
              onChange={(on) => setChanged({ ...changed, [f.kind]: on })}
            />
          ))}
        </div>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {making.state === "making" && <Notice><p>{w("copies.making")}</p></Notice>}
      {making.state === "ready" && <Notice tone="success"><p>{w("copies.ready")}</p></Notice>}
      {making.state === "done" && <Notice tone="success"><p>{w("copies.downloaded")}</p></Notice>}
      {making.state === "failed" && <Notice tone="error"><p>{w("copies.failed")}</p></Notice>}
      <div className="av-close-actions">
        {making.state === "ready" ? (
          <Button onClick={() => download(making.job)}>{w("copies.download")}</Button>
        ) : (
          <Button variant="secondary" disabled={making.state === "making" || chosen.length === 0} onClick={make}>
            {w("copies.download_n" as CloseKey, { count: chosen.length })}
          </Button>
        )}
      </div>
    </Section>
  );
}

function CopyKind(props: { label: string; count: number; checked: boolean; disabled: boolean; onChange: (on: boolean) => void }) {
  const id = useId();
  return (
    <label className="av-close-copy">
      <input type="checkbox" checked={props.checked} disabled={props.disabled} aria-labelledby={`${id}-l`} aria-describedby={`${id}-n`} onChange={(e) => props.onChange(e.target.checked)} />
      <span id={`${id}-l`}>{props.label}</span>
      <span id={`${id}-n`} className="av-visually-hidden">{w("copies.count" as CloseKey, { count: props.count })}</span>
    </label>
  );
}

function Option(props: { name: string; value: string; checked: boolean; disabled?: boolean; label: string; hint?: string | null; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <label className={`av-close-option${props.checked ? " av-close-option--on" : ""}${props.disabled ? " av-close-option--disabled" : ""}`}>
      <input
        type="radio"
        name={props.name}
        value={props.value}
        checked={props.checked}
        disabled={props.disabled}
        aria-labelledby={`${id}-label`}
        aria-describedby={props.hint ? `${id}-hint` : undefined}
        onChange={() => props.onChange(props.value)}
      />
      <span>
        <span id={`${id}-label`}>{props.label}</span>
        {props.hint && (
          <span id={`${id}-hint`} className="av-muted av-close-option__hint">
            {props.hint}
          </span>
        )}
      </span>
    </label>
  );
}

function Keep(props: { caseId: string; summary: Summary; canDeleteNow: boolean; party: string | null; onSaved: () => void }) {
  const { summary } = props;
  const stepUp = useStepUp();
  // With nothing saved yet the usual setting is the one in force, so it shows chosen.
  const saved = summary.retention_policy ?? "keep_default";
  const [choice, setChoice] = useState<string>(saved);
  const [confirming, setConfirming] = useState(false);
  const busy = useBusy();
  const [message, setMessage] = useState<Message>(null);
  const limit = summary.max_retention_years ?? null;
  const tooLong = limit !== null && summary.retention_years > limit; // the organisation keeps records for less
  const deleteNowBlocked = summary.ever_served || summary.held;
  const deleteNowHint = summary.ever_served ? w("keep.delete_now_served") : summary.held ? w("keep.delete_now_held") : w("keep.delete_now_hint");

  const save = async (policy: Policy) => {
    setMessage(null);
    try {
      const done = await stepUp(() =>
        unwrap(api.PUT("/v1/cases/{case_id}/retention", { params: { path: { case_id: props.caseId } }, body: { policy } })),
      );
      if (done) {
        setMessage({ tone: "success", text: w("keep.saved") });
        props.onSaved();
      }
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
    }
  };

  const pick = (value: string) => {
    if (value === "delete_now") {
      setConfirming(true);
      return;
    }
    setChoice(value);
    void busy.run(() => save(value as Policy));
  };

  return (
    <Section title={w("keep.heading")} step={2}>
      <p>{w("keep.lead")}</p>
      <fieldset className="av-choices">
        <legend className="av-visually-hidden">{w("keep.legend")}</legend>
        {props.canDeleteNow && (
          <Option name="policy" value="delete_now" checked={choice === "delete_now"} disabled={deleteNowBlocked || busy.busy} label={w("keep.delete_now")} hint={deleteNowHint} onChange={pick} />
        )}
        <Option name="policy" value="keep_default" checked={choice === "keep_default"} disabled={tooLong || busy.busy} label={w("keep.keep_default", { years: summary.retention_years })} hint={tooLong ? w("keep.limited", { years: limit }) : w("keep.usual")} onChange={pick} />
        <Option
          name="policy"
          value="return_then_delete"
          checked={choice === "return_then_delete"}
          disabled={busy.busy}
          label={props.party ? w("keep.return_then_delete", { party: props.party }) : w("keep.return_then_delete_plain")}
          onChange={pick}
        />
      </fieldset>
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      {confirming && (
        <Dialog title={w("keep.confirm_title")} onClose={() => setConfirming(false)}>
          <p>{w("keep.confirm_body")}</p>
          <div className="av-close-actions">
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              {w("keep.confirm_no")}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirming(false);
                setChoice("delete_now");
                void busy.run(() => save("delete_now"));
              }}
            >
              {w("keep.confirm_yes")}
            </Button>
          </div>
        </Dialog>
      )}
    </Section>
  );
}

function Proof(props: { caseLabel: string; party: string | null; summary: Summary; locale: string }) {
  const { summary, locale } = props;
  const date = summary.delete_after ? formatDate(summary.delete_after, locale) : null;
  let status = w("proof.not_scheduled");
  if (summary.held) status = w("proof.paused");
  else if (date) status = w("proof.scheduled", { date });
  const name = w("proof.certificate");
  return (
    <Section title={w("proof.heading")} step={3}>
      <p>{props.party ? w("proof.lead_party", { party: props.party }) : w("proof.lead")}</p>
      <div role="group" aria-label={name} className="av-close-cert">
        <p className="av-close-cert__title" aria-hidden="true">{name}</p>
        <p>{w("proof.case", { case: props.caseLabel })}</p>
        <p>{w("proof.records")}</p>
        <p>{date && !summary.held ? w("proof.deleted_on", { date }) : w("proof.date_pending")}</p>
        <p>{w("proof.kept")}</p>
      </div>
      <p className="av-close-scheduled">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></svg>
        {status}
      </p>
    </Section>
  );
}

function Hold(props: { caseId: string; summary: Summary; locale: string; canHold: boolean; canRelease: boolean; onChanged: () => void }) {
  const { summary, locale } = props;
  const stepUp = useStepUp();
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const active = summary.holds.filter((h) => h.released_at === null);
  const until = active.map((h) => h.until).filter((u): u is string => u !== null).sort().at(-1);
  const pausedText = active.some((h) => h.until === null) || !until ? w("hold.paused") : w("hold.paused_until", { date: formatDate(until, locale) });

  const resume = async () => {
    setError(null);
    try {
      for (const hold of active) {
        await stepUp(() =>
          unwrap(api.POST("/v1/cases/{case_id}/legal-holds/{hold_id}/release", { params: { path: { case_id: props.caseId, hold_id: hold.id } } })),
        );
      }
      props.onChanged();
    } catch (err) {
      setError(errorMessage(err, OWN_ERRORS));
    }
  };

  if (!props.canHold || summary.deletion_started) return null;
  return (
    <section className="av-card av-close-hold" aria-labelledby={id}>
      <div className="av-close-hold__text">
        <h2 id={id}>{w("hold.question")}</h2>
        <p className="av-muted">{w("hold.lead")}</p>
        {summary.held && (
          <Notice tone="warning">
            <p>{pausedText}</p>
          </Notice>
        )}
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
      </div>
      {summary.held ? (
        props.canRelease && (
          <Button variant="secondary" onClick={resume}>
            {w("hold.resume")}
          </Button>
        )
      ) : (
        <Button variant="secondary" onClick={() => setAsking(true)}>
          {w("hold.pause")}
        </Button>
      )}
      {asking && (
        <PauseDialog
          caseId={props.caseId}
          onClose={() => setAsking(false)}
          onDone={() => {
            setAsking(false);
            props.onChanged();
          }}
        />
      )}
    </section>
  );
}

function PauseDialog({ caseId, onClose, onDone }: { caseId: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState<string | null>(null);
  const [until, setUntil] = useState("");
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    if (!reason) return;
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/legal-holds", { params: { path: { case_id: caseId } }, body: { reason, until: until || null } }));
      onDone();
    } catch (err) {
      setError(errorMessage(err, OWN_ERRORS));
    }
  };
  return (
    <Dialog title={w("hold.dialog_title")} onClose={onClose}>
      <div className="av-stack">
        <fieldset className="av-choices">
          <legend>{w("hold.reason")}</legend>
          {HOLD_REASONS.map((r) => (
            <Option key={r} name="hold-reason" value={r} checked={reason === r} label={w(`hold.reason.${r}`)} onChange={setReason} />
          ))}
        </fieldset>
        <TextField label={w("hold.until")} hint={w("hold.until_hint")} name="until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
        <div className="av-close-actions">
          <Button variant="secondary" onClick={onClose}>
            {w("hold.cancel")}
          </Button>
          <Button disabled={!reason} onClick={confirm}>
            {w("hold.confirm")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function Closing(props: { party: string | null; caseId: string; status: string; can: (a: string) => boolean; onDone: () => void; setMessage: (m: Message) => void }) {
  const { can, status } = props;
  const path = { case_id: props.caseId };
  const run = async (call: () => Promise<unknown>) => {
    props.setMessage(null);
    try {
      await call();
      props.setMessage({ tone: "success", text: w("close.done") });
      props.onDone();
    } catch (err) {
      props.setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
    }
  };
  let button: ReactNode = null;
  if ((status === "active" || status === "served") && can("case.settle")) {
    button = <Button arrow onClick={() => run(() => unwrap(api.POST("/v1/cases/{case_id}/settle", { params: { path } })))}>{w("close.settle")}</Button>;
  } else if ((status === "settled" || status === "withdrawn") && can("case.archive")) {
    button = <Button arrow onClick={() => run(() => unwrap(api.POST("/v1/cases/{case_id}/archive", { params: { path } })))}>{w("close.archive")}</Button>;
  } else if (status === "archived" && can("case.reopen")) {
    button = <Button variant="secondary" onClick={() => run(() => unwrap(api.POST("/v1/cases/{case_id}/reopen", { params: { path } })))}>{w("close.reopen")}</Button>;
  }
  if (!button) return null;
  return (
    <div className="av-close-foot">
      <p className="av-muted">{props.party ? w("close.note_party", { party: props.party }) : w("close.note")}</p>
      <div className="av-close-actions">{button}</div>
    </div>
  );
}

function DeletedCase({ caseId }: { caseId: string }) {
  return (
    <div className="av-page av-stack">
      <h1>{w("heading.deleted")}</h1>
      <Certificate caseId={caseId} locale={undefined} />
    </div>
  );
}

function Certificate({ caseId, locale }: { caseId: string; locale: string | undefined }) {
  const cert = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deletion-certificate", { params: { path: { case_id: caseId } } })), [caseId], { cache: "close/Close#Certificate:cert" });
  if (cert.error) return <LoadError onRetry={cert.reload} />;
  if (!cert.data) return <Loading />;
  const counts = cert.data.counts as Record<string, number>;
  const files = Number(counts.storage_objects ?? 0);
  const items = Object.entries(counts).reduce((sum, [k, v]) => (k === "storage_objects" ? sum : sum + Number(v ?? 0)), 0);
  return (
    <Section title={w("proof.certificate")}>
      <dl className="av-close-facts">
        <dt>{w("proof.when")}</dt>
        <dd>{formatDate(cert.data.deleted_at, locale)}</dd>
        <dt>{w("proof.items")}</dt>
        <dd>{items}</dd>
        <dt>{w("proof.stored_files")}</dt>
        <dd>{files}</dd>
      </dl>
      {cert.data.backup_expiry_on && <p className="av-muted">{w("proof.backups", { date: formatDate(cert.data.backup_expiry_on, locale) })}</p>}
    </Section>
  );
}
