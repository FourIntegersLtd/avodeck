// S18b Time and invoice (docs/design/screens/S18b-Invoice.dc.html): time by workflow step (step names from the
// pack) with audited adjustments beside a paper invoice; Download and Send invoice at the bottom (sending behind
// the code); payments and credit notes; a sent invoice never changes, mistakes are put right with a credit note.
// The time estimate per step, the billing details and the bank details sit behind one link at the foot. When the
// time runs over the estimate by more than the pack's threshold the server sends a gentle notice naming the
// instructing party (in the pack's word), which never blocks anything.
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Dialog, IconTile, LoadError, Loading, Notice, SectionIcon, stepLabel, TextField } from "../../foundation/ui";
import { longDate, money, useLocale } from "./shared";
import { w } from "./words";

type Invoice = Schemas["InvoiceOut"];
const ERROR_CODES = ["no_billing_profile", "no_hourly_rate", "already_sent", "invalid", "stale", "reason_needed"] as const;
const ERRORS: Record<string, string> = Object.fromEntries(ERROR_CODES.map((code) => [code, `report.invoice.error.${code}`]));
const PAYMENT = ["paid", "part_paid", "written_off"] as const;

export function InvoiceScreen() {
  const { caseId = "" } = useParams();
  const report = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#InvoiceScreen:report" });
  const version = report.data?.version ?? null;
  const sent = version?.status === "served";
  const locale = useLocale();
  const servings = useResource(
    () => (sent && version ? unwrap(api.GET("/v1/cases/{case_id}/report/versions/{version_id}/servings", { params: { path: { case_id: caseId, version_id: version.id } } })) : Promise.resolve(null)),
    [caseId, version?.id, sent],
  );
  const time = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/time", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#InvoiceScreen:time" });
  const served = servings.data?.servings.at(-1);
  const sentTo = sent && served && time.data?.instructing_party ? w("invoice.sent_to_on", { party: time.data.instructing_party, date: longDate(served.served_on, locale) }) : null;
  return (
    <div className="av-page rp-page rp-page--wide2">
      <div className="rp-hero rp-hero--small">
        {sent && (
          <IconTile tone="mint">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </IconTile>
        )}
        <div>
          <h1 className="rp-title">{sent ? w("invoice.heading_sent") : w("invoice.title")}</h1>
          <p className="rp-lead">{sentTo ? `${sentTo} ${w("invoice.intro")}` : w("invoice.intro")}</p>
        </div>
      </div>
      <Workspace caseId={caseId} />
    </div>
  );
}

function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  return w("invoice.hours", { hours: Math.floor(total / 3600), minutes: Math.floor((total % 3600) / 60) });
}

async function loadProfile(): Promise<{ profile: Schemas["BillingProfile"] | null; etag: string }> {
  const result = await api.GET("/v1/me/billing-profile");
  const body = await unwrap(Promise.resolve(result));
  return { profile: body.profile, etag: result.response.headers.get("etag") ?? '"0"' };
}

/** The time card and the paper invoice side by side, then the bottom bar and the forms behind one link. */
function Workspace({ caseId }: { caseId: string }) {
  const stepUp = useStepUp();
  const navigate = useNavigate();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/invoices", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#Workspace:found" });
  const billing = useResource(loadProfile, []);
  const theCase = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#Workspace:theCase" });
  const [expenses, setExpenses] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    try {
      if ((await work()) !== undefined) found.reload();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const create = () =>
    act(async () => {
      const made = await unwrap(api.POST("/v1/cases/{case_id}/invoices", { params: { path: { case_id: caseId } }, body: { expenses: expenses.trim() || "0" } }));
      setExpenses("");
      return made;
    });
  if (found.error || billing.error) return <LoadError onRetry={() => { found.reload(); billing.reload(); }} />;
  if (!found.data || !billing.data) return <Loading inline />;
  const list = found.data.invoices;
  const latest = list.at(-1) ?? null;
  const missing = billing.data.profile === null && !saved;
  const from = billing.data.profile?.legal_name ?? null;
  const caseLabel = theCase.data?.label ?? null;
  const pdf = (invoice: Invoice) => `${apiOrigin()}/v1/cases/${caseId}/invoices/${invoice.id}/pdf`;
  const send = async (invoice: Invoice) => {
    setError(null);
    try {
      const out = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/invoices/{invoice_id}/send", { params: { path: { case_id: caseId, invoice_id: invoice.id } }, body: { to: null } })));
      if (out === undefined) return;
      const next = screenPath("S19", { caseId });
      if (next) navigate(next);
      else found.reload();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <>
      {missing && <Notice tone="amber"><p>{w("invoice.billing_missing")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <div className="rp-grid">
        <TimeCard caseId={caseId} />
        {list.length === 0 ? (
          <article className="rp-paper rp-paper--empty" aria-label={w("invoice.paper_title")}>
            <h3 className="rp-paper__title">{w("invoice.paper_title")}</h3>
            <p className="av-muted">{w("invoice.none")}</p>
            <TextField label={w("invoice.expenses")} name="expenses" inputMode="decimal" value={expenses} onChange={(e) => setExpenses(e.target.value)} />
          </article>
        ) : (
          list.map((invoice) => (
            <InvoicePaper key={invoice.id} caseId={caseId} invoice={invoice} from={from} caseLabel={caseLabel} act={act} download={invoice === latest ? null : pdf(invoice)} />
          ))
        )}
      </div>
      <div className="rp-bar">
        <span className="av-muted rp-bar__note">{w("invoice.fee_note")}</span>
        <div className="rp-bar__actions">
          {latest ? (
            <>
              <a className="av-button av-button--secondary" href={pdf(latest)}>{w("invoice.download")}</a>
              {latest.status === "draft" && <Button arrow onClick={() => send(latest)}>{w("invoice.send")}</Button>}
            </>
          ) : (
            <Button arrow onClick={() => create()}>{w("invoice.create")}</Button>
          )}
        </div>
      </div>
      <details className="rp-fold" open={missing || undefined}>
        <summary className="av-text-link">{w("invoice.more")}</summary>
        <div className="av-stack">
          <EstimateCard caseId={caseId} />
          <BillingForm initial={billing.data} onSaved={() => setSaved(true)} />
          {latest && (
            <section className="av-card av-stack" aria-labelledby="another-title">
              <h3 id="another-title">{w("invoice.list")}</h3>
              <TextField label={w("invoice.expenses")} name="expenses" inputMode="decimal" value={expenses} onChange={(e) => setExpenses(e.target.value)} />
              <div className="av-row"><Button variant="secondary" onClick={() => create()}>{w("invoice.create")}</Button></div>
            </section>
          )}
        </div>
      </details>
    </>
  );
}

function TimeCard({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/time", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#TimeCard:found" });
  const [adjusting, setAdjusting] = useState(false);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading inline />;
  const time = found.data;
  return (
    <>
      <section className="rp-card rp-time" aria-labelledby="time-title">
        <div className="rp-time__head">
          <h2 id="time-title" className="rp-card__title">{w("invoice.time")}</h2>
          <button type="button" className="av-text-link rp-linkbtn" onClick={() => setAdjusting(true)}>{w("invoice.adjust")}</button>
        </div>
        <ul className="rp-time__list">
          {time.stages.map((s) => (
            <li key={s.stage} aria-label={stepLabel(s.stage)} className="rp-time__row">
              <span>{stepLabel(s.stage)}</span>
              <meter className="rp-time__bar" min={0} max={Math.max(time.total_seconds, 1)} value={s.seconds} aria-label={stepLabel(s.stage)} />
              <span className="av-mono rp-time__value">{duration(s.seconds)}</span>
            </li>
          ))}
          <li className="rp-time__row rp-time__total">
            <strong>{w("invoice.total")}</strong>
            <strong className="av-mono rp-time__value">{duration(time.total_seconds)}</strong>
          </li>
        </ul>
        {time.adjusted && <p className="av-muted rp-small">{w("invoice.adjusted")}</p>}
        <p className="av-muted rp-small">{w("invoice.time_note")}</p>
        {adjusting && <Adjust caseId={caseId} stages={time.stages} onClose={() => setAdjusting(false)} onSaved={() => { setAdjusting(false); found.reload(); }} />}
      </section>
      {time.estimate_hours != null && !time.over_estimate && time.instructing_party && (
        <div className="rp-within"><Notice tone="mint">
          <p className="rp-within__line">
            <svg className="rp-tick" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
            <strong>{w("invoice.within_estimate_lead")}</strong>{" "}
            {w("invoice.within_estimate_rest", { party: time.instructing_party, hours: Number(time.estimate_hours) })}
          </p>
        </Notice></div>
      )}
    </>
  );
}

function BillingForm({ initial, onSaved }: { initial: { profile: Schemas["BillingProfile"] | null; etag: string }; onSaved: () => void }) {
  const [etag, setEtag] = useState(initial.etag);
  const [missing, setMissing] = useState(initial.profile === null);
  return (
    <>
      <BillingDetails initial={initial.profile} etag={etag} onSaved={(tag) => { setEtag(tag); setMissing(false); onSaved(); }} />
      {!missing && <BankForm initial={initial.profile?.bank ?? null} etag={etag} onSaved={setEtag} />}
    </>
  );
}

function InvoicePaper({ caseId, invoice, from, caseLabel, act, download }: { caseId: string; invoice: Invoice; from: string | null; caseLabel: string | null; act: (work: () => Promise<unknown>) => Promise<void>; download: string | null }) {
  const locale = useLocale();
  const [part, setPart] = useState("");
  const [crediting, setCrediting] = useState(false);
  const base = { params: { path: { case_id: caseId, invoice_id: invoice.id } } };
  const money_ = (amount: string | number) => money(amount, invoice.currency, locale);
  const pay = (status: "part_paid" | "paid" | "written_off", paid_amount?: string) =>
    act(() => unwrap(api.PATCH("/v1/cases/{case_id}/invoices/{invoice_id}/payment", { ...base, body: paid_amount ? { status, paid_amount } : { status } })));
  const label = invoice.number_text ? w("invoice.number", { number: invoice.number_text }) : w("invoice.draft");
  const isDraft = invoice.status === "draft";
  const open = invoice.status === "sent" || invoice.status === "part_paid";
  const paymentWord = (PAYMENT as readonly string[]).includes(invoice.status) ? w(`invoice.payment_${invoice.status as (typeof PAYMENT)[number]}`) : w("invoice.payment_unpaid");

  return (
    <article className="rp-paper" aria-label={label}>
      <div className="rp-paper__head">
        <h3 className="rp-paper__title">{w("invoice.paper_title")}</h3>
        {invoice.number_text ? <span className="av-mono rp-paper__number">{invoice.number_text}</span> : <span className="av-badge">{w("invoice.status.draft")}</span>}
      </div>
      <div className="rp-paper__who">
        {from && <p>{w("invoice.from", { name: from })}</p>}
        {invoice.sent_to && <p>{w("invoice.to", { name: invoice.sent_to })}</p>}
        {caseLabel && <p>{w("invoice.case", { name: caseLabel })}</p>}
        <p>{w("invoice.date", { date: longDate(invoice.sent_at ?? invoice.created_at, locale) })}</p>
      </div>
      <ul className="rp-paper__lines">
        {invoice.lines.map((line, i) => (
          <li key={i}>
            <span>{line.kind === "time" ? w("invoice.line_time", { hours: String(line.hours), rate: money_(String(line.rate)) }) : w("invoice.line_expenses")}</span>
            <span>{money_(String(line.amount))}</span>
          </li>
        ))}
        {Number(invoice.tax) > 0 && (
          <li><span>{w("invoice.tax")}</span><span>{money_(invoice.tax)}</span></li>
        )}
        <li className="rp-paper__total">
          <strong>{w("invoice.total_due")}</strong>
          <strong>{money_(invoice.total)}</strong>
        </li>
      </ul>
      {invoice.estimate_amount != null && (
        <p className="rp-paper__small" data-estimate="true">{w("invoice.estimate_line", { estimate: money_(invoice.estimate_amount), amount: money_(invoice.total) })}</p>
      )}
      {invoice.sent_at && <p className="rp-paper__small">{w("invoice.sent", { date: longDate(invoice.sent_at, locale) })}</p>}
      {Number(invoice.paid_amount) > 0 && <p className="rp-paper__small">{w("invoice.paid_so_far", { amount: money_(invoice.paid_amount) })}</p>}
      {invoice.credit_notes.map((c) => (
        <p key={c.id} className="rp-paper__small">{w("invoice.credit_line", { number: c.number_text ?? "", amount: money_(c.amount) })}</p>
      ))}
      {invoice.statements.map((line) => (
        <p key={line} className="rp-paper__small">{line}</p>
      ))}
      <p className="rp-paper__small">{w("invoice.locked_note")}</p>
      <div className="rp-payment">
        <span><strong>{w("invoice.payment")}</strong> <span>{paymentWord}</span></span>
        {open && <Button variant="link" className="av-text-link rp-linkbtn" onClick={() => pay("paid")}>{w("invoice.mark_paid")}</Button>}
      </div>
      {(open || !isDraft || download) && (
        <details className="rp-fold rp-fold--inner">
          <summary className="av-text-link">{w("invoice.more_payment")}</summary>
          <div className="av-stack">
            {download && <a className="av-button av-button--secondary" href={download}>{w("invoice.download")}</a>}
            {open && (
              <>
                <div className="av-row">
                  <TextField label={w("invoice.part_amount")} name="paid_amount" inputMode="decimal" value={part} onChange={(e) => setPart(e.target.value)} />
                  <Button variant="secondary" disabled={!part.trim()} onClick={() => pay("part_paid", part.trim())}>{w("invoice.mark_part")}</Button>
                </div>
                <div className="av-row"><Button variant="quiet" onClick={() => pay("written_off")}>{w("invoice.written_off")}</Button></div>
              </>
            )}
            {!isDraft && <div className="av-row"><Button variant="secondary" onClick={() => setCrediting(true)}>{w("invoice.credit")}</Button></div>}
          </div>
        </details>
      )}
      {crediting && <Credit caseId={caseId} invoiceId={invoice.id} onClose={() => setCrediting(false)} onSaved={() => { setCrediting(false); void act(() => Promise.resolve(true)); }} />}
    </article>
  );
}

function EstimateCard({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/time", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Invoice#EstimateCard:found" });
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading inline />;
  return <EstimateForm caseId={caseId} time={found.data} />;
}

type Entry = { hours: string; fee: string };

function EstimateForm({ caseId, time }: { caseId: string; time: Schemas["TimeOut"] }) {
  const initial = (s: Schemas["StageTime"]): Entry => ({ hours: s.estimate_hours ?? "", fee: s.estimate_fee ?? "" });
  const [current, setCurrent] = useState(time);
  const [entries, setEntries] = useState<Record<string, Entry>>(() => Object.fromEntries(time.stages.map((s) => [s.stage, initial(s)])));
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (stage: string, field: keyof Entry, value: string) => {
    setSaved(false);
    setEntries((all) => ({ ...all, [stage]: { ...(all[stage] ?? { hours: "", fee: "" }), [field]: value } }));
  };
  const save = async () => {
    setError(null);
    const estimates = current.stages.flatMap((s) => {
      const entry = entries[s.stage] ?? { hours: "", fee: "" };
      const hours = entry.hours.trim();
      const fee = entry.fee.trim();
      const was = initial(s);
      if (!hours || (hours === was.hours && fee === was.fee)) return [];
      return [fee ? { stage: s.stage, hours, fee } : { stage: s.stage, hours }];
    });
    if (estimates.length === 0) return;
    try {
      const out = await unwrap(api.POST("/v1/cases/{case_id}/time/estimates", { params: { path: { case_id: caseId } }, body: { estimates } }));
      if (out.stages.length > 0) setCurrent(out);
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const over = current.over_estimate;
  return (
    <section className="av-card av-stack" aria-labelledby="estimate-title">
      <h3 id="estimate-title">{w("invoice.estimate")}</h3>
      <p className="av-muted">{w("invoice.estimate_intro")}</p>
      {over && (
        <div className="av-notice av-notice--warning" role="status" aria-labelledby="over-estimate-title">
          <p id="over-estimate-title"><strong>{w("invoice.over_estimate_title")}</strong></p>
          <p>{w("invoice.over_estimate", { percent: over.percent, party: over.instructing_party })}</p>
        </div>
      )}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {current.stages.map((s) => (
        <fieldset key={s.stage} className="av-stack" aria-label={stepLabel(s.stage)}>
          <legend>{stepLabel(s.stage)}</legend>
          <div className="av-row">
            <TextField label={w("invoice.estimate_hours")} name={`estimate-hours-${s.stage}`} inputMode="decimal" value={entries[s.stage]?.hours ?? ""} onChange={(e) => set(s.stage, "hours", e.target.value)} />
            <TextField label={w("invoice.estimate_fee")} name={`estimate-fee-${s.stage}`} inputMode="decimal" value={entries[s.stage]?.fee ?? ""} onChange={(e) => set(s.stage, "fee", e.target.value)} />
          </div>
        </fieldset>
      ))}
      {current.estimate_hours && <p>{w("invoice.estimate_total", { hours: current.estimate_hours })}</p>}
      <div className="av-row">
        <Button variant="secondary" onClick={() => save()}>{w("invoice.estimate_save")}</Button>
        {saved && <span role="status">{w("invoice.estimate_saved")}</span>}
      </div>
    </section>
  );
}

function Adjust({ caseId, stages, onClose, onSaved }: { caseId: string; stages: Schemas["StageTime"][]; onClose: () => void; onSaved: () => void }) {
  const [stage, setStage] = useState(stages[0]?.stage ?? "");
  const [minutes, setMinutes] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    const value = Number(minutes);
    if (!reason.trim() || !minutes.trim() || !Number.isFinite(value)) return setError(w("invoice.error.invalid"));
    try {
      await unwrap(api.PATCH("/v1/cases/{case_id}/time/adjust", { params: { path: { case_id: caseId } }, body: { stage, seconds: Math.round(value * 60), reason: reason.trim() } }));
      onSaved();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <Dialog title={w("invoice.adjust_title")} onClose={onClose}>
      <div className="av-stack">
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <div className="av-field">
          <label className="av-field__label" htmlFor="adjust-stage">{w("invoice.adjust_stage")}</label>
          <select id="adjust-stage" className="av-input" value={stage} onChange={(e) => setStage(e.target.value)}>
            {stages.map((s) => (
              <option key={s.stage} value={s.stage}>{stepLabel(s.stage)}</option>
            ))}
          </select>
        </div>
        <TextField label={w("invoice.adjust_minutes")} name="minutes" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        <TextField label={w("invoice.adjust_reason")} name="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="av-row">
          <Button onClick={() => save()}>{w("invoice.adjust_save")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("invoice.cancel")}</Button>
        </div>
      </div>
    </Dialog>
  );
}

function BillingDetails({ initial: p, etag, onSaved }: { initial: Schemas["BillingProfile"] | null; etag: string; onSaved: (etag: string) => void }) {
  const [name, setName] = useState(p?.legal_name ?? "");
  const [address, setAddress] = useState(p?.address ?? "");
  const [taxNumber, setTaxNumber] = useState(p?.tax_number ?? "");
  const [taxRate, setTaxRate] = useState(p?.tax_rate ?? "0");
  const [currency, setCurrency] = useState(p?.currency ?? "");
  const [rate, setRate] = useState(p?.hourly_rate ?? "");
  const [prefix, setPrefix] = useState(p?.invoice_prefix ?? "");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    setSaved(false);
    try {
      const result = await api.PUT("/v1/me/billing-profile", {
        headers: ifMatch(etag),
        body: { legal_name: name, address: address || null, tax_number: taxNumber || null, tax_rate: taxRate || "0", currency, hourly_rate: rate || null, invoice_prefix: prefix },
      });
      await unwrap(Promise.resolve(result));
      onSaved(result.response.headers.get("etag") ?? etag);
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby="billing-title">
      <SectionIcon glyph="invoice" />
      <h3 id="billing-title">{w("invoice.billing")}</h3>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("invoice.billing_name")} name="legal_name" value={name} onChange={(e) => setName(e.target.value)} />
      <TextField label={w("invoice.billing_address")} name="address" value={address} onChange={(e) => setAddress(e.target.value)} />
      <TextField label={w("invoice.billing_tax_number")} name="tax_number" value={taxNumber} onChange={(e) => setTaxNumber(e.target.value)} />
      <TextField label={w("invoice.billing_tax_rate")} name="tax_rate" inputMode="decimal" value={taxRate} onChange={(e) => setTaxRate(e.target.value)} />
      <TextField label={w("invoice.billing_currency")} name="currency" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
      <TextField label={w("invoice.billing_rate")} name="hourly_rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
      <TextField label={w("invoice.billing_prefix")} name="invoice_prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
      <div className="av-row">
        <Button variant="secondary" onClick={() => save()}>{w("invoice.billing_save")}</Button>
        {saved && <span role="status">{w("invoice.billing_saved")}</span>}
      </div>
    </section>
  );
}

type Bank = NonNullable<Schemas["BillingProfile"]["bank"]>;
const BANK_FIELDS = ["account_name", "sort_code", "account_number", "iban", "bic"] as const;

function BankForm({ initial, etag, onSaved }: { initial: Bank | null; etag: string; onSaved: (etag: string) => void }) {
  const stepUp = useStepUp();
  const [values, setValues] = useState<Record<(typeof BANK_FIELDS)[number], string>>(() =>
    Object.fromEntries(BANK_FIELDS.map((f) => [f, initial?.[f] ?? ""])) as Record<(typeof BANK_FIELDS)[number], string>,
  );
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    setSaved(false);
    const body = Object.fromEntries(BANK_FIELDS.map((f) => [f, values[f].trim() || null])) as Bank;
    try {
      const out = await stepUp(async () => {
        const result = await api.PUT("/v1/me/billing-profile/bank-details", { headers: ifMatch(etag), body });
        await unwrap(Promise.resolve(result));
        return result;
      });
      if (out) {
        onSaved(out.response.headers.get("etag") ?? etag);
        setSaved(true);
      }
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby="bank-title">
      <SectionIcon glyph="lock" />
      <h3 id="bank-title">{w("invoice.bank")}</h3>
      <p className="av-muted">{w("invoice.bank_note")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {BANK_FIELDS.map((f) => (
        <TextField key={f} label={w(`invoice.bank_${f}`)} name={`bank_${f}`} value={values[f]} onChange={(e) => { const v = e.target.value; setValues((all) => ({ ...all, [f]: v })); }} />
      ))}
      <div className="av-row">
        <Button variant="secondary" onClick={() => save()}>{w("invoice.bank_save")}</Button>
        {saved && <span role="status">{w("invoice.billing_saved")}</span>}
      </div>
    </section>
  );
}

function Credit({ caseId, invoiceId, onClose, onSaved }: { caseId: string; invoiceId: string; onClose: () => void; onSaved: () => void }) {
  const stepUp = useStepUp();
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!amount.trim() || !reason.trim()) return setError(w("invoice.error.reason_needed"));
    try {
      const out = await stepUp(() =>
        unwrap(api.POST("/v1/cases/{case_id}/invoices/{invoice_id}/credit-notes", { params: { path: { case_id: caseId, invoice_id: invoiceId } }, body: { amount: amount.trim(), reason: reason.trim() } })),
      );
      if (out) onSaved();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <Dialog title={w("invoice.credit_title")} onClose={onClose}>
      <div className="av-stack">
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <TextField label={w("invoice.credit_amount")} name="amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <TextField label={w("invoice.credit_reason")} name="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="av-row">
          <Button onClick={() => save()}>{w("invoice.credit_save")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("invoice.cancel")}</Button>
        </div>
      </div>
    </Dialog>
  );
}
