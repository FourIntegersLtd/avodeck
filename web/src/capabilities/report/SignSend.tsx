// S18 Sign and send (docs/design/screens/S18-SignSend.dc.html): the document and the saved signature, the
// declaration wording from the pack, one confirm box, signing (behind the code), a note on who it goes to, then
// marking it as sent (also behind the code); a served report is shown locked and read-only with where it went.
import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, Dialog, IconTile, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
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
  const report = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId]);
  const check = useResource(() => latestCheck(caseId), [caseId]);
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path: { case_id: caseId } } })), [caseId]);
  if (report.error || check.error || pack.error) return <div className="av-page"><LoadError onRetry={() => { report.reload(); check.reload(); pack.reload(); }} /></div>;
  if (report.data === undefined || check.data === undefined || !pack.data) return <div className="av-page"><Loading /></div>;
  const version = report.data?.version ?? null;
  if (!version) {
    return (
      <div className="av-page rp-page rp-page--narrow">
        <h1 className="rp-title">{w("sign.title")}</h1>
        <Notice><p>{w("sign.nothing")}</p></Notice>
        <div className="rp-bar"><span /><LinkButton to={`/cases/${caseId}/report`}>{w("report.title")}</LinkButton></div>
      </div>
    );
  }
  return <SignView caseId={caseId} version={version} check={check.data} declarations={pack.data.declarations} onChanged={report.reload} />;
}

function Paper() {
  return (
    <svg className="rp-thumb" width="96" height="128" viewBox="0 0 96 128" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="95" height="127" rx="2" className="rp-thumb__page" />
      <rect x="10" y="14" width="34" height="5" className="rp-thumb__title" />
      <path d="M10 27h76M10 35h76M10 43h60M10 51h76M10 59h40" className="rp-thumb__lines" />
    </svg>
  );
}

function SignView({ caseId, version, check, declarations, onChanged }: { caseId: string; version: Version; check: Schemas["FinalCheckOut"] | null; declarations: Schemas["CasePack"]["declarations"]; onChanged: () => void }) {
  const locale = useLocale();
  const { pack } = useCaseFrame();
  const status = version.status;
  const ready = !!check && check.passed && check.current;
  const appendices = (pack.report_appendices ?? []).length;
  const downloads = (
    <div className="av-row">
      <a className="av-button av-button--secondary" href={`${apiOrigin()}${downloadPath(caseId, version.id, "docx")}`}>{w("sign.download_word")}</a>
      <a className="av-button av-button--secondary" href={`${apiOrigin()}${downloadPath(caseId, version.id, "pdf")}`}>{w("sign.download_pdf")}</a>
    </div>
  );
  const doc = (
    <section className="rp-card rp-doc" aria-labelledby="doc-title">
      <Paper />
      <div>
        <h3 id="doc-title" className="rp-doc__title">{version.display_name}</h3>
        <p className="av-muted">
          {version.page_count ? <span>{appendices > 0 ? w("sign.pages_appendices", { pages: version.page_count, count: appendices }) : w("sign.pages", { pages: version.page_count })}</span> : null}{" "}
          {status === "draft" && !ready && <span className="av-badge">{w("sign.not_ready")}</span>}
          {status === "locked" && <span className="av-badge">{w("sign.locked")}</span>}
          {status === "served" && <span className="av-badge av-badge--on">{w("sign.served")}</span>}
          {status === "withdrawn" && <span className="av-badge">{w("sign.withdrawn")}</span>}
        </p>
        <p className="av-muted">{w("sign.version_dated", { date: longDate(version.created_at, locale) })}</p>
      </div>
    </section>
  );
  return (
    <div className="av-page rp-page rp-page--narrow">
      <div className="rp-hero rp-hero--small">
        {status === "draft" && ready && (
          <IconTile tone="mint">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </IconTile>
        )}
        <div>
          <h1 className="rp-title">{status === "draft" && ready ? w("sign.title_ready") : w("sign.title")}</h1>
          {status === "draft" && ready && check && <p className="rp-lead">{w("sign.checks_passed", { count: check.results.length })}</p>}
        </div>
      </div>
      {status === "draft" && (
        <>
          {!ready && (
            <Notice tone="warning">
              <LinkButton variant="secondary" to={`/cases/${caseId}/final-check`}>{w("sign.go_check")}</LinkButton>
            </Notice>
          )}
          <Draft caseId={caseId} doc={doc} declarations={declarations} downloads={downloads} onDone={onChanged} />
        </>
      )}
      {status !== "draft" && <div className="rp-pair">{doc}</div>}
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
          <div className="rp-bar">
            {status === "served" ? <Withdraw caseId={caseId} versionId={version.id} onDone={onChanged} /> : <span />}
            <LinkButton arrow to={`/cases/${caseId}/invoice`}>{w("sign.invoice")}</LinkButton>
          </div>
        </>
      )}
    </div>
  );
}

/** "Name, profession · body number", leaving out whatever the profile does not have. */
function signatureLine(p: Schemas["Profile"]): string {
  const registration = [p.registration_body, p.registration_number].filter(Boolean).join(" ");
  const named = [p.full_name, p.profession].filter(Boolean).join(", ");
  return [named, registration].filter(Boolean).join(" · ");
}

function Draft({ caseId, doc, declarations, downloads, onDone }: { caseId: string; doc: ReactNode; declarations: Schemas["CasePack"]["declarations"]; downloads: ReactNode; onDone: () => void }) {
  const stepUp = useStepUp();
  const { item, pack } = useCaseFrame();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; code: string } | null>(null);
  const [read, setRead] = useState(false);
  const [signed, setSigned] = useState(false);
  const [noPicture, setNoPicture] = useState(false);
  const me = useResource(() => unwrap(api.GET("/v1/me/profile")), []);
  const vocabulary = pack.vocabulary as Record<string, unknown>;
  const declarationName = typeof vocabulary.declaration_name === "string" ? vocabulary.declaration_name : null;
  const party = (pack.instructing_party_types as Array<{ key: string; label?: string }>).find((p) => p.key === item.instructing_party_type)?.label ?? null;

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
    <>
      <div className="rp-pair">
        {doc}
        <section className="rp-card rp-signature" aria-labelledby="sig-title">
          <h3 id="sig-title" className="rp-card__title">{w("sign.signature")}</h3>
          {noPicture ? (
            <p className="av-muted">{w("sign.signature_body")}</p>
          ) : (
            <img className="rp-signature__image" src={`${apiOrigin()}/v1/me/signature/image`} alt={w("sign.signature_alt")} onError={() => setNoPicture(true)} />
          )}
          {me.data?.full_name && <p className="rp-signature__line">{signatureLine(me.data)}</p>}
        </section>
      </div>
      <section className="rp-card" aria-labelledby="decl-title">
        <h2 id="decl-title" className="rp-card__title">{declarationName ? declarationName.charAt(0).toUpperCase() + declarationName.slice(1) : w("sign.declaration")}</h2>
        <div className="rp-declaration">
          {declarations.map((d) =>
            (d.text ?? "").split("\n\n").map((part) => (
              <p key={`${d.key}-${part}`} className="av-report-declaration">{part}</p>
            )),
          )}
        </div>
        <Checkbox label={declarationName ? w("sign.confirm", { declaration: declarationName.toLowerCase() }) : w("sign.confirm_plain")} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
      </section>
      <p className="rp-note">{party ? w("sign.note", { party }) : w("sign.note_plain")}</p>
      {error && (
        <Notice tone="error">
          <p>{error.message}</p>
          {error.code === "declaration_not_acknowledged" && <Button variant="secondary" onClick={() => void acknowledge()}>{w("sign.acknowledge")}</Button>}
        </Notice>
      )}
      {read && <Notice tone="success"><p>{w("sign.acknowledged")}</p></Notice>}
      {signed && <Notice tone="success"><p>{w("sign.signed")}</p></Notice>}
      <div className="rp-bar">
        {downloads}
        <Button arrow disabled={!confirmed || busy} onClick={() => void sign()}>{busy ? w("sign.signing") : party ? w("sign.submit", { party }) : w("sign.submit_plain")}</Button>
      </div>
    </>
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
    <section className="rp-card av-stack" aria-labelledby="serve-title">
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
      <div className="rp-bar">
        <span />
        <Button arrow onClick={() => void serve()} disabled={!servedOn}>{w("sign.serve")}</Button>
      </div>
    </section>
  );
}

function Servings({ caseId, versionId }: { caseId: string; versionId: string }) {
  const locale = useLocale();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/versions/{version_id}/servings", { params: { path: { case_id: caseId, version_id: versionId } } })), [caseId, versionId]);
  if (!found.data || found.data.servings.length === 0) return null;
  return (
    <section className="rp-card av-stack" aria-labelledby="servings-title">
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
