// S21 New records and the other expert's report (docs/design/screens/S21-NewMaterial.dc.html). May-matter lines show
// only where the server returned them; a statement is called a difference only after the expert confirms it.
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { keyName, useLocale, useWaiting, versionTag } from "./shared";
import { w } from "./words";

type Claim = Schemas["OpposingClaimOut"];
const ERRORS: Record<string, string> = {
  stale: "after_serving.error.stale",
  invalid: "after_serving.error.invalid",
  not_checked: "after_serving.new.error.not_checked",
  read_first: "after_serving.new.error.read_first",
};

// A check's result shows only once it exists and the viewer has read our page (the server decides).
const shown = (c: Claim) => c.status !== "matches" && c.status !== "unchecked" && c.status !== "read_first";

export function NewMaterial() {
  const { caseId = "" } = useParams();
  const [params] = useSearchParams();
  const document = params.get("document");
  const locale = useLocale();
  const path = { params: { path: { case_id: caseId } } };
  const pages = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/new-since-served", path)), [caseId]);
  const report = useResource(async () => {
    if (!document) return null;
    try {
      return await unwrap(api.GET("/v1/cases/{case_id}/opposing-reports/{document_id}", { params: { path: { case_id: caseId, document_id: document } } }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }, [caseId, document]);
  const checking = useWaiting(() => pages.reload(), pages.data?.pages.length);
  const registering = useWaiting(() => report.reload(), report.data?.claims.length);
  const [error, setError] = useState<string | null>(null);

  if (pages.error || report.error) return <div className="av-page"><LoadError onRetry={() => { pages.reload(); report.reload(); }} /></div>;
  if (!pages.data || report.data === undefined) return <div className="av-page"><Loading /></div>;
  const list = pages.data.pages;
  const fmt = (n: number) => new Intl.NumberFormat(locale).format(n);
  const unread = list.find((p) => !p.read);
  const check = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/new-since-served/check", path));
      checking.start();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };

  return (
    <div className="av-page av-stack">
      <LinkButton variant="quiet" to={`/cases/${caseId}/served`}>{w("back")}</LinkButton>
      <h2>{w("new.title")}</h2>
      <p>{w("new.intro")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {checking.waiting && <Notice><p>{w("new.checking")}</p></Notice>}
      {list.length === 0 ? (
        <Notice><p>{w("new.none")}</p></Notice>
      ) : (
        <section className="av-card av-stack">
          <h3>{w("new.pages", { count: list.length })}</h3>
          <p className="av-after-mono">{w("new.range", { from: fmt(Math.min(...list.map((p) => p.page_no))), to: fmt(Math.max(...list.map((p) => p.page_no))), read: list.filter((p) => p.read).length, total: list.length })}</p>
          <ul className="av-after-list">
            {list.flatMap((p) => p.may_matter.map((m) => (
              <li key={`${p.page_id}-${m.fact_id}`} className="av-after-item av-after-flag">{w("new.may_matter", { why: m.why, page: p.page_no })}</li>
            )))}
          </ul>
          <div className="av-row">
            {unread && <LinkButton to={`/cases/${caseId}/read/${unread.page_no}`}>{w("new.read_action")}</LinkButton>}
            <Button variant="secondary" onClick={() => void check()}>{w("new.check")}</Button>
          </div>
        </section>
      )}
      <section className="av-card av-stack" aria-labelledby="opposing">
        <h3 id="opposing">{w("new.opposing")}</h3>
        {registering.waiting && <Notice><p>{w("new.registering")}</p></Notice>}
        {!document && <p>{w("new.opposing_document_needed")}</p>}
        {document && !report.data && !registering.waiting && <Register caseId={caseId} document={document} onStart={() => registering.start()} />}
        {report.data && <Claims caseId={caseId} report={report.data} meeting={screenPath("expert_meeting", { caseId })} />}
      </section>
      <Link className="av-sr-only" to={`/cases/${caseId}/served`} tabIndex={-1} aria-hidden="true" />
    </div>
  );
}

function Register({ caseId, document, onStart }: { caseId: string; document: string; onStart: () => void }) {
  const [receivedOn, setReceivedOn] = useState("");
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/opposing-reports", { params: { path: { case_id: caseId } }, body: { document_id: document, received_on: receivedOn } }));
      onStart();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <div className="av-stack">
      <p>{w("new.opposing_none")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("new.received_on")} name="received_on" type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} />
      <div className="av-after-footer">
        <Button disabled={!receivedOn} onClick={() => void go()}>{w("new.register")}</Button>
      </div>
    </div>
  );
}

function Claims({ caseId, report, meeting }: { caseId: string; report: Schemas["OpposingReport"]; meeting: string | null }) {
  const [over, setOver] = useState<Record<string, Claim>>({});
  const [error, setError] = useState<string | null>(null);
  const confirm = async (c: Claim) => {
    setError(null);
    try {
      const out = await unwrap(api.PATCH("/v1/cases/{case_id}/opposing-claims/{claim_id}", { params: { path: { case_id: caseId, claim_id: c.id } }, body: { confirm: true }, headers: ifMatch(versionTag(c.version)) }));
      setOver((o) => ({ ...o, [c.id]: out }));
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const needAttention = report.claims.filter((c) => c.discrepancy && !c.expert_confirmed).length;
  const matching = report.claims.filter((c) => c.status === "matches");
  const rows = report.claims.filter((c) => c.status !== "matches");
  const folded = matching.slice(1);
  const rowFor = (base: Claim) => {
    const c = over[base.id] ?? base;
    return (
      <tr key={c.id}>
        <td className="av-after-words">{c.text_verbatim}</td>
        <td className="av-after-mono">{c.their_page_ref ? `p.${c.their_page_ref}` : ""}</td>
        <td className="av-stack">
          <strong>{c.status === "read_first" ? w("new.read_first", { page: c.our_page_no ?? "" }) : keyName("new.status", c.status)}</strong>
          {shown(c) && (
            <div className="av-after-columns">
              <div><h5>{w("new.their_quote")}</h5><p className="av-after-words">{c.their_quote}</p></div>
              <div><h5>{w("new.our_quote", { page: c.our_page_no ?? "" })}</h5><p className="av-after-words">{c.our_quote}</p></div>
            </div>
          )}
          {c.expert_confirmed
            ? <p role="status">{w("new.confirmed")}</p>
            : c.discrepancy && shown(c) && <Button variant="secondary" onClick={() => void confirm(c)}>{w("new.confirm")}</Button>}
        </td>
      </tr>
    );
  };
  return (
    <div className="av-stack">
      <p>{w("new.checked", { count: needAttention })}</p>
      <h4 id="claims-title">{w("new.claims")}</h4>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <table className="av-after-table" aria-labelledby="claims-title">
        <thead>
          <tr>
            <th scope="col">{w("new.theirs")}</th>
            <th scope="col">{w("new.reference")}</th>
            <th scope="col">{w("new.found")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(rowFor)}
          {matching.slice(0, 1).map(rowFor)}
          {folded.length > 0 && (
            <tr><td colSpan={3} className="av-muted">{w("new.more_matches", { count: folded.length })}</td></tr>
          )}
        </tbody>
      </table>
      {report.positions.length > 0 && (
        <section className="av-stack" aria-labelledby="differ-title">
          <h4 id="differ-title">{w("new.positions")}</h4>
          {report.positions.map((p) => (
            <div key={p.question_id} className="av-after-columns">
              <div><h5>{w("new.you")}</h5>{p.own.map((o) => <p key={o.paragraph_id} className="av-after-words">{o.text}</p>)}</div>
              <div><h5>{w("new.them")}</h5>{p.theirs.map((o) => <p key={o.paragraph_ref} className="av-after-words">{o.text_verbatim}</p>)}</div>
            </div>
          ))}
          {meeting && <div className="av-after-footer"><LinkButton to={meeting} arrow>{w("new.prepare")}</LinkButton></div>}
        </section>
      )}
    </div>
  );
}
