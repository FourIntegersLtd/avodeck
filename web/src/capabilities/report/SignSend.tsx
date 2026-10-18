// S18 Sign and send (docs/design/screens/S18-SignSend.dc.html): the declaration wording from the pack, one confirm
// box, signing and marking as sent (both behind the code), downloads, and a served report shown locked and
// read-only with where it went. The saved signature is added by the server; nothing is drawn here.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, Dialog, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { useTimeHeartbeat } from "./heartbeat";
import { downloadPath, longDate, today, useLocale } from "./shared";
import { w } from "./words";

type Version = Schemas["VersionOut"];
const VIAS = ["email", "post", "portal", "hand", "other"] as const;
const SIGN_ERRORS = ["final_check_needed", "final_check_failed", "citation_problems", "declaration_not_acknowledged", "signature_unavailable", "not_built", "already_served", "not_locked", "hash_mismatch"] as const;
const ERRORS: Record<string, string> = Object.fromEntries(SIGN_ERRORS.map((code) => [code, `report.sign.error.${code}`]));

async function latestCheck(caseId: string): Promise<Schemas["FinalCheckOut"] | null> {
  try {
    return await unwrap(api.GET("/v1/cases/{case_id}/report/final-check/latest", { params: { path: { case_id: caseId } } }));
  } catch (err) {
    if (err instanceof ApiError && err.code === "not_found") return null;
    throw err;
  }
}

export function SignSend() {
  const { caseId = "" } = useParams();
  useTimeHeartbeat(caseId, "sign_send");
  const report = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId]);
  const check = useResource(() => latestCheck(caseId), [caseId]);
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path: { case_id: caseId } } })), [caseId]);
  if (report.error || check.error || pack.error) return <div className="av-page"><LoadError onRetry={() => { report.reload(); check.reload(); pack.reload(); }} /></div>;
  if (report.data === undefined || check.data === undefined || !pack.data) return <div className="av-page"><Loading /></div>;
  const version = report.data?.version ?? null;
  if (!version) {
    return (
      <div className="av-page av-stack">
        <h2>{w("sign.title")}</h2>
        <Notice><p>{w("sign.nothing")}</p></Notice>
        <div className="av-report-footer"><LinkButton to={`/cases/${caseId}/report`}>{w("report.title")}</LinkButton></div>
      </div>
    );
  }
  return <SignView caseId={caseId} version={version} check={check.data} declarations={pack.data.declarations} onChanged={report.reload} />;
}

function SignView({ caseId, version, check, declarations, onChanged }: { caseId: string; version: Version; check: Schemas["FinalCheckOut"] | null; declarations: Schemas["CasePack"]["declarations"]; onChanged: () => void }) {
  const status = version.status;
  const ready = !!check && check.passed && check.current;
  const downloads = (
    <div className="av-row">
      <a className="av-button av-button--secondary" href={`${apiOrigin()}${downloadPath(caseId, version.id, "docx")}`}>{w("sign.download_word")}</a>
      <a className="av-button av-button--secondary" href={`${apiOrigin()}${downloadPath(caseId, version.id, "pdf")}`}>{w("sign.download_pdf")}</a>
    </div>
  );
  return (
    <div className="av-page av-stack">
      <h2>{w("sign.title")}</h2>
      <p>
        <strong>{version.page_count ? w("sign.version", { name: version.display_name, pages: version.page_count }) : version.display_name}</strong>{" "}
        {status === "draft" && <span className={ready ? "av-badge av-badge--on" : "av-badge"}>{ready ? w("sign.ready") : w("sign.not_ready")}</span>}
        {status === "locked" && <span className="av-badge">{w("sign.locked")}</span>}
        {status === "served" && <span className="av-badge av-badge--on">{w("sign.served")}</span>}
        {status === "withdrawn" && <span className="av-badge">{w("sign.withdrawn")}</span>}
      </p>
      {status === "draft" && (
        <>
          {!ready && (
            <Notice tone="warning">
              <LinkButton variant="secondary" to={`/cases/${caseId}/final-check`}>{w("sign.go_check")}</LinkButton>
            </Notice>
          )}
          <Draft caseId={caseId} declarations={declarations} downloads={downloads} onDone={onChanged} />
        </>
      )}
      {status === "locked" && (
        <>
          {downloads}
          <Serve caseId={caseId} versionId={version.id} onDone={onChanged} />
        </>
      )}
      {(status === "served" || status === "withdrawn") && (
        <>
          <Notice><p>{w("sign.served_note")}</p></Notice>
          {downloads}
          <Servings caseId={caseId} versionId={version.id} />
          <div className="av-report-footer">
            {status === "served" && <Withdraw caseId={caseId} versionId={version.id} onDone={onChanged} />}
            <LinkButton to={`/cases/${caseId}/invoice`}>{w("sign.invoice")}</LinkButton>
          </div>
        </>
      )}
    </div>
  );
}

function Draft({ caseId, declarations, downloads, onDone }: { caseId: string; declarations: Schemas["CasePack"]["declarations"]; downloads: React.ReactNode; onDone: () => void }) {
  const stepUp = useStepUp();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; code: string } | null>(null);
  const [read, setRead] = useState(false);
  const [signed, setSigned] = useState(false);

  const sign = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/report/sign", { params: { path: { case_id: caseId } }, body: { declaration_confirmed: true } })));
      if (out) {
        setSigned(true);
        onDone();
      }
    } catch (err) {
      setError({ message: errorMessage(err, ERRORS), code: err instanceof ApiError ? err.code : "" });
    } finally {
      setBusy(false);
    }
  };

  const acknowledge = async () => {
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/report/declarations/acknowledge", { params: { path: { case_id: caseId } } }));
      setRead(true);
      setError(null);
    } catch (err) {
      setError({ message: errorMessage(err, ERRORS), code: "" });
    }
  };

  return (
    <div className="av-stack">
      <section className="av-card av-stack" aria-labelledby="sig-title">
        <h3 id="sig-title">{w("sign.signature")}</h3>
        <p>{w("sign.signature_body")}</p>
      </section>
      <section className="av-card av-stack" aria-labelledby="decl-title">
        <h3 id="decl-title">{w("sign.declaration")}</h3>
        {declarations.map((d) => (
          <div key={d.key} className="av-stack">
            {(d.text ?? "").split("\n\n").map((part) => (
              <p key={part} className="av-report-declaration">{part}</p>
            ))}
          </div>
        ))}
        <Checkbox label={w("sign.confirm")} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
      </section>
      <p className="av-muted">{w("sign.lock_note")}</p>
      {error && (
        <Notice tone="error">
          <p>{error.message}</p>
          {error.code === "declaration_not_acknowledged" && <Button variant="secondary" onClick={() => void acknowledge()}>{w("sign.acknowledge")}</Button>}
        </Notice>
      )}
      {read && <Notice tone="success"><p>{w("sign.acknowledged")}</p></Notice>}
      {signed && <Notice tone="success"><p>{w("sign.signed")}</p></Notice>}
      {downloads}
      <div className="av-report-footer">
        <Button disabled={!confirmed || busy} onClick={() => void sign()}>{busy ? w("sign.signing") : w("sign.submit")}</Button>
      </div>
    </div>
  );
}

function Serve({ caseId, versionId, onDone }: { caseId: string; versionId: string; onDone: () => void }) {
  const stepUp = useStepUp();
  const [servedOn, setServedOn] = useState(today());
  const [via, setVia] = useState<(typeof VIAS)[number]>("email");
  const [error, setError] = useState<string | null>(null);
  const serve = async () => {
    setError(null);
    try {
      const out = await stepUp(() =>
        unwrap(api.POST("/v1/cases/{case_id}/report/versions/{version_id}/serve", { params: { path: { case_id: caseId, version_id: versionId } }, body: { served_on: servedOn, sent_via: via, recipient_party_id: null } })),
      );
      if (out) onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby="serve-title">
      <h3 id="serve-title">{w("sign.serve")}</h3>
      <Notice tone="success"><p>{w("sign.signed")}</p></Notice>
      <p>{w("sign.serve_intro")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("sign.served_on")} name="served_on" type="date" value={servedOn} onChange={(e) => setServedOn(e.target.value)} />
      <div className="av-field">
        <label className="av-field__label" htmlFor="sent-via">{w("sign.sent_via")}</label>
        <select id="sent-via" className="av-input" value={via} onChange={(e) => setVia(e.target.value as (typeof VIAS)[number])}>
          {VIAS.map((v) => (
            <option key={v} value={v}>{w(`sign.via.${v}`)}</option>
          ))}
        </select>
      </div>
      <div className="av-report-footer">
        <Button onClick={() => void serve()} disabled={!servedOn}>{w("sign.serve")}</Button>
      </div>
    </section>
  );
}

function Servings({ caseId, versionId }: { caseId: string; versionId: string }) {
  const locale = useLocale();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/versions/{version_id}/servings", { params: { path: { case_id: caseId, version_id: versionId } } })), [caseId, versionId]);
  if (!found.data || found.data.servings.length === 0) return null;
  return (
    <section className="av-card av-stack" aria-labelledby="servings-title">
      <h3 id="servings-title">{w("sign.servings")}</h3>
      <ul className="av-report-list">
        {found.data.servings.map((s) => (
          <li key={s.id} className="av-report-item">
            {w("sign.serving", { date: longDate(s.served_on, locale), via: (VIAS as readonly string[]).includes(s.sent_via) ? w(`sign.via.${s.sent_via as (typeof VIAS)[number]}`) : s.sent_via })}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Withdraw({ caseId, versionId, onDone }: { caseId: string; versionId: string; onDone: () => void }) {
  const stepUp = useStepUp();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const confirm = async () => {
    if (!reason.trim()) return setProblem(w("sign.reason_needed"));
    setProblem(null);
    try {
      const out = await stepUp(() =>
        unwrap(api.POST("/v1/cases/{case_id}/report/versions/{version_id}/withdraw", { params: { path: { case_id: caseId, version_id: versionId } }, body: { reason: reason.trim() } })),
      );
      if (out) {
        setOpen(false);
        onDone();
      }
    } catch (err) {
      setProblem(errorMessage(err, ERRORS));
    }
  };
  return (
    <>
      <Button variant="danger" onClick={() => setOpen(true)}>{w("sign.withdraw")}</Button>
      {open && (
        <Dialog title={w("sign.withdraw_title")} onClose={() => setOpen(false)}>
          <div className="av-stack">
            <TextField label={w("sign.withdraw_reason")} name="reason" value={reason} onChange={(e) => setReason(e.target.value)} error={problem ?? undefined} />
            <div className="av-row">
              <Button variant="danger" onClick={() => void confirm()}>{w("sign.withdraw_confirm")}</Button>
              <Button variant="secondary" onClick={() => setOpen(false)}>{w("sign.cancel")}</Button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
}
