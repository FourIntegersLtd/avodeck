// S18b Time and invoice (docs/design/screens/S18b-Invoice.dc.html): time by workflow step (step names from the
// pack), audited adjustments, the expert's billing details, invoices made from the time, sending behind the code,
// payments and credit notes. A sent invoice never changes; mistakes are put right with a credit note. The time
// estimate per step sits beside the time; when the time runs over it by more than the pack's threshold the server
// sends a gentle notice naming the instructing party (in the pack's word), which never blocks anything. Bank
// details for the invoice's "How to pay" box are saved behind the code.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Dialog, LoadError, Loading, Notice, stepLabel, TextField } from "../../foundation/ui";
import { longDate, money, useLocale } from "./shared";
import { w } from "./words";

type Invoice = Schemas["InvoiceOut"];
const ERROR_CODES = ["no_billing_profile", "no_hourly_rate", "already_sent", "invalid", "stale", "reason_needed"] as const;
const ERRORS: Record<string, string> = Object.fromEntries(ERROR_CODES.map((code) => [code, `report.invoice.error.${code}`]));
const STATUSES = ["draft", "sent", "part_paid", "paid", "written_off"] as const;

export function InvoiceScreen() {
  const { caseId = "" } = useParams();
  return (
    <div className="av-page av-stack">
      <h2>{w("invoice.title")}</h2>
      <p>{w("invoice.intro")}</p>
      <TimeCard caseId={caseId} />
      <EstimateCard caseId={caseId} />
      <BillingCard />
      <Invoices caseId={caseId} />
    </div>
  );
}

function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  return w("invoice.hours", { hours: Math.floor(total / 3600), minutes: Math.floor((total % 3600) / 60) });
}

function TimeCard({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/time", { params: { path: { case_id: caseId } } })), [caseId]);
  const [adjusting, setAdjusting] = useState(false);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading />;
  const time = found.data;
  return (
    <section className="av-card av-stack" aria-labelledby="time-title">
      <div className="av-row av-row--spread">
        <h3 id="time-title">{w("invoice.time")}</h3>
        <Button variant="secondary" onClick={() => setAdjusting(true)}>{w("invoice.adjust")}</Button>
      </div>
      <ul className="av-report-list">
        {time.stages.map((s) => (
          <li key={s.stage} aria-label={stepLabel(s.stage)} className="av-report-item av-report-item--spread">
            <span>{stepLabel(s.stage)}</span>
            <span className="av-report-mono">{duration(s.seconds)}</span>
          </li>
        ))}
        <li className="av-report-item av-report-item--spread">
          <strong>{w("invoice.total")}</strong>
          <strong className="av-report-mono">{duration(time.total_seconds)}</strong>
        </li>
      </ul>
      {time.adjusted && <p className="av-muted">{w("invoice.adjusted")}</p>}
      <p className="av-muted">{w("invoice.time_note")}</p>
      {adjusting && <Adjust caseId={caseId} stages={time.stages} onClose={() => setAdjusting(false)} onSaved={() => { setAdjusting(false); found.reload(); }} />}
    </section>
  );
}

function EstimateCard({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/time", { params: { path: { case_id: caseId } } })), [caseId]);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading />;
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
        <Button variant="secondary" onClick={() => void save()}>{w("invoice.estimate_save")}</Button>
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
          <Button onClick={() => void save()}>{w("invoice.adjust_save")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("invoice.cancel")}</Button>
        </div>
      </div>
    </Dialog>
  );
}

async function loadProfile(): Promise<{ profile: Schemas["BillingProfile"] | null; etag: string }> {
  const result = await api.GET("/v1/me/billing-profile");
  const body = await unwrap(Promise.resolve(result));
  return { profile: body.profile, etag: result.response.headers.get("etag") ?? '"0"' };
}

function BillingCard() {
  const found = useResource(loadProfile, []);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading />;
  return <BillingForm initial={found.data} />;
}

function BillingForm({ initial }: { initial: { profile: Schemas["BillingProfile"] | null; etag: string } }) {
  const [etag, setEtag] = useState(initial.etag);
  const [missing, setMissing] = useState(initial.profile === null);
  return (
    <>
      <BillingDetails initial={initial.profile} etag={etag} missing={missing} onSaved={(tag) => { setEtag(tag); setMissing(false); }} />
      {!missing && <BankForm initial={initial.profile?.bank ?? null} etag={etag} onSaved={setEtag} />}
    </>
  );
}

function BillingDetails({ initial: p, etag, missing, onSaved }: { initial: Schemas["BillingProfile"] | null; etag: string; missing: boolean; onSaved: (etag: string) => void }) {
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
      <h3 id="billing-title">{w("invoice.billing")}</h3>
      {missing && <Notice tone="warning"><p>{w("invoice.billing_missing")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("invoice.billing_name")} name="legal_name" value={name} onChange={(e) => setName(e.target.value)} />
      <TextField label={w("invoice.billing_address")} name="address" value={address} onChange={(e) => setAddress(e.target.value)} />
      <TextField label={w("invoice.billing_tax_number")} name="tax_number" value={taxNumber} onChange={(e) => setTaxNumber(e.target.value)} />
      <TextField label={w("invoice.billing_tax_rate")} name="tax_rate" inputMode="decimal" value={taxRate} onChange={(e) => setTaxRate(e.target.value)} />
      <TextField label={w("invoice.billing_currency")} name="currency" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
      <TextField label={w("invoice.billing_rate")} name="hourly_rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
      <TextField label={w("invoice.billing_prefix")} name="invoice_prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
      <div className="av-row">
        <Button variant="secondary" onClick={() => void save()}>{w("invoice.billing_save")}</Button>
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
      <h3 id="bank-title">{w("invoice.bank")}</h3>
      <p className="av-muted">{w("invoice.bank_note")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {BANK_FIELDS.map((f) => (
        <TextField key={f} label={w(`invoice.bank_${f}`)} name={`bank_${f}`} value={values[f]} onChange={(e) => { const v = e.target.value; setValues((all) => ({ ...all, [f]: v })); }} />
      ))}
      <div className="av-row">
        <Button variant="secondary" onClick={() => void save()}>{w("invoice.bank_save")}</Button>
        {saved && <span role="status">{w("invoice.billing_saved")}</span>}
      </div>
    </section>
  );
}

function Invoices({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/invoices", { params: { path: { case_id: caseId } } })), [caseId]);
  const [expenses, setExpenses] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/invoices", { params: { path: { case_id: caseId } }, body: { expenses: expenses.trim() || "0" } }));
      setExpenses("");
      found.reload();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading />;
  const list = found.data.invoices;
  return (
    <section className="av-card av-stack" aria-labelledby="invoices-title">
      <h3 id="invoices-title">{w("invoice.list")}</h3>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {list.length === 0 && <p className="av-muted">{w("invoice.none")}</p>}
      {list.map((invoice) => (
        <InvoiceCard key={invoice.id} caseId={caseId} invoice={invoice} onChanged={found.reload} />
      ))}
      <TextField label={w("invoice.expenses")} name="expenses" inputMode="decimal" value={expenses} onChange={(e) => setExpenses(e.target.value)} />
      <div className="av-report-footer">
        <Button onClick={() => void create()}>{w("invoice.create")}</Button>
      </div>
    </section>
  );
}

function InvoiceCard({ caseId, invoice, onChanged }: { caseId: string; invoice: Invoice; onChanged: () => void }) {
  const locale = useLocale();
  const stepUp = useStepUp();
  const [error, setError] = useState<string | null>(null);
  const [part, setPart] = useState("");
  const [crediting, setCrediting] = useState(false);
  const base = { params: { path: { case_id: caseId, invoice_id: invoice.id } } };
  const money_ = (amount: string | number) => money(amount, invoice.currency, locale);
  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    try {
      if ((await work()) !== undefined) onChanged();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const pay = (status: "part_paid" | "paid" | "written_off", paid_amount?: string) =>
    act(() => unwrap(api.PATCH("/v1/cases/{case_id}/invoices/{invoice_id}/payment", { ...base, body: paid_amount ? { status, paid_amount } : { status } })));
  const label = invoice.number_text ? w("invoice.number", { number: invoice.number_text }) : w("invoice.draft");
  const status = (STATUSES as readonly string[]).includes(invoice.status) ? w(`invoice.status.${invoice.status as (typeof STATUSES)[number]}`) : invoice.status;

  return (
    <article className="av-report-invoice av-stack" aria-label={label}>
      <div className="av-row av-row--spread">
        <h4>{label}</h4>
        <span className="av-badge">{status}</span>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <ul className="av-report-list">
        {invoice.lines.map((line, i) => (
          <li key={i} className="av-report-item av-report-item--spread">
            <span>{line.kind === "time" ? w("invoice.line_time", { hours: String(line.hours), rate: money_(String(line.rate)) }) : w("invoice.line_expenses")}</span>
            <span className="av-report-mono">{money_(String(line.amount))}</span>
          </li>
        ))}
        {Number(invoice.tax) > 0 && (
          <li className="av-report-item av-report-item--spread"><span>{w("invoice.tax")}</span><span className="av-report-mono">{money_(invoice.tax)}</span></li>
        )}
        <li className="av-report-item av-report-item--spread">
          <strong>{w("invoice.total_due")}</strong>
          <strong className="av-report-mono">{money_(invoice.total)}</strong>
        </li>
      </ul>
      {invoice.estimate_amount != null && (
        <p data-estimate="true">{w("invoice.estimate_line", { estimate: money_(invoice.estimate_amount), amount: money_(invoice.total) })}</p>
      )}
      {invoice.sent_at && <p className="av-muted">{w("invoice.sent", { date: longDate(invoice.sent_at, locale) })}</p>}
      {Number(invoice.paid_amount) > 0 && <p className="av-muted">{w("invoice.paid_so_far", { amount: money_(invoice.paid_amount) })}</p>}
      {invoice.credit_notes.map((c) => (
        <p key={c.id} className="av-muted">{w("invoice.credit_line", { number: c.number_text ?? "", amount: money_(c.amount) })}</p>
      ))}
      <p className="av-muted">{w("invoice.locked_note")}</p>
      <div className="av-row">
        <a className="av-button av-button--secondary" href={`${apiOrigin()}/v1/cases/${caseId}/invoices/${invoice.id}/pdf`}>{w("invoice.download")}</a>
        {invoice.status === "draft" && (
          <Button onClick={() => void act(() => stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/invoices/{invoice_id}/send", { ...base, body: { to: null } }))))}>{w("invoice.send")}</Button>
        )}
        {invoice.status !== "draft" && <Button variant="secondary" onClick={() => setCrediting(true)}>{w("invoice.credit")}</Button>}
      </div>
      {(invoice.status === "sent" || invoice.status === "part_paid") && (
        <div className="av-stack">
          <div className="av-row">
            <Button variant="secondary" onClick={() => void pay("paid")}>{w("invoice.mark_paid")}</Button>
            <Button variant="quiet" onClick={() => void pay("written_off")}>{w("invoice.written_off")}</Button>
          </div>
          <div className="av-row">
            <TextField label={w("invoice.part_amount")} name="paid_amount" inputMode="decimal" value={part} onChange={(e) => setPart(e.target.value)} />
            <Button variant="secondary" disabled={!part.trim()} onClick={() => void pay("part_paid", part.trim())}>{w("invoice.mark_part")}</Button>
          </div>
        </div>
      )}
      {crediting && <Credit caseId={caseId} invoiceId={invoice.id} onClose={() => setCrediting(false)} onSaved={() => { setCrediting(false); onChanged(); }} />}
    </article>
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
          <Button onClick={() => void save()}>{w("invoice.credit_save")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("invoice.cancel")}</Button>
        </div>
      </div>
    </Dialog>
  );
}
