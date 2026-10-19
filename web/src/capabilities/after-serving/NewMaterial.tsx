// S21 New records and the other expert's report (docs/design/screens/S21-NewMaterial.dc.html). May-matter lines show
// only where the server returned them; a statement is called a difference only after the expert confirms it.
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, IconTile, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { PeopleIcon, NewDocIcon, TickGreenIcon, WarnIcon } from "./icons";
import { dayMonth, keyName, useLocale, useWaiting, versionTag } from "./shared";
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

  const rep = report.data;
  const needAttention = rep ? rep.claims.filter((c) => c.discrepancy && !c.expert_confirmed).length : 0;
  const meeting = screenPath("expert_meeting", { caseId });

  return (
    <div className="av-page av-after-page av-stack">
      <div>
        <BackLink to={`/cases/${caseId}/served`}>{w("back")}</BackLink>
        <h2>{w("new.title")}</h2>
        <p className="av-muted av-after-lede">{w("new.intro")}</p>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {checking.waiting && <Notice><p>{w("new.checking")}</p></Notice>}
      <div className="av-after-pair">
        <section className="av-after-tile av-after-tile--plain" aria-labelledby="new-pages">
          <div className="av-after-tile__head">
            <IconTile><NewDocIcon /></IconTile>
            <span>
              <h3 id="new-pages">{list.length === 0 ? w("new.title_none") : w("new.pages", { count: list.length })}</h3>
              {list.length > 0 && <span className="av-muted">{w("new.range", { from: fmt(Math.min(...list.map((p) => p.page_no))), to: fmt(Math.max(...list.map((p) => p.page_no))), read: list.filter((p) => p.read).length, total: list.length })}</span>}
            </span>
          </div>
          {list.length === 0 ? (
            <p>{w("new.none")}</p>
          ) : (
            <>
              {list.some((p) => p.may_matter.length > 0) && (
                <ul className="av-after-maymatter">
                  {list.flatMap((p) => p.may_matter.map((m) => (
                    <li key={`${p.page_id}-${m.fact_id}`}>{w("new.may_matter", { why: m.why, page: p.page_no })}</li>
                  )))}
                </ul>
              )}
              <div className="av-after-actions">
                {unread && <LinkButton to={`/cases/${caseId}/read/${unread.page_no}`}>{w("new.read_action")}</LinkButton>}
                <button type="button" className="av-after-linkbtn" onClick={() => void check()}>{w("new.check")}</button>
              </div>
            </>
          )}
        </section>
        <section className="av-after-tile av-after-tile--plain" aria-labelledby="opposing">
          <div className="av-after-tile__head">
            <IconTile><PeopleIcon /></IconTile>
            <span>
              <h3 id="opposing">{w("new.opposing")}</h3>
              {rep && <span className="av-muted">{w("new.received_short", { date: dayMonth(rep.received_on, locale) })}</span>}
            </span>
          </div>
          {registering.waiting && <Notice><p>{w("new.registering")}</p></Notice>}
          {!document && <p>{w("new.opposing_document_needed")}</p>}
          {document && !rep && !registering.waiting && <Register caseId={caseId} document={document} onStart={() => registering.start()} />}
          {rep && <p>{w("new.checked", { count: needAttention })}</p>}
        </section>
      </div>
      {rep && <Claims caseId={caseId} report={rep} />}
      {rep && meeting && <div className="av-after-footer"><LinkButton to={meeting} arrow>{w("new.prepare")}</LinkButton></div>}
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

function Claims({ caseId, report }: { caseId: string; report: Schemas["OpposingReport"] }) {
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
  const matching = report.claims.filter((c) => c.status === "matches");
  const rows = report.claims.filter((c) => c.status !== "matches");
  const folded = matching.slice(1);
  const rowFor = (base: Claim) => {
    const c = over[base.id] ?? base;
    const good = c.status === "matches";
    return (
      <tr key={c.id}>
        <td>{c.text_verbatim}</td>
        <td className="av-after-mono">{c.their_page_ref ? `p.${c.their_page_ref}` : ""}</td>
        <td className="av-stack">
          <strong className={good ? "av-after-found av-after-found--good" : "av-after-found"}>
            {good ? <TickGreenIcon /> : <WarnIcon />}
            {c.status === "read_first" ? w("new.read_first", { page: c.our_page_no ?? "" }) : keyName("new.status", c.status)}
          </strong>
          {shown(c) && (
            <div className="av-after-columns">
              <div><h5>{w("new.their_quote")}</h5><p className="av-after-words">{c.their_quote}</p></div>
              <div><h5>{w("new.our_quote", { page: c.our_page_no ?? "" })}</h5><p className="av-after-words">{c.our_quote}</p></div>
            </div>
          )}
          {c.expert_confirmed
            ? <p role="status">{w("new.confirmed")}</p>
            : c.discrepancy && shown(c) && <div><Button variant="secondary" onClick={() => void confirm(c)}>{w("new.confirm")}</Button></div>}
        </td>
      </tr>
    );
  };
  return (
    <>
      <section className="av-after-panel">
        <h3 id="claims-title">{w("new.claims")}</h3>
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <table className="av-after-claims" aria-labelledby="claims-title">
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
      </section>
      {report.positions.length > 0 && (
        <section className="av-after-panel av-stack" aria-labelledby="differ-title">
          <h3 id="differ-title">{w("new.positions")}</h3>
          {report.positions.map((p) => (
            <div key={p.question_id} className="av-after-differ">
              <div className="av-after-differ__you">
                <strong>{w("new.you")}:</strong> {p.own.map((o) => <span key={o.paragraph_id}>{o.text}</span>)}
              </div>
              <div className="av-after-differ__them">
                <strong>{w("new.them")}:</strong> {p.theirs.map((o) => <span key={o.paragraph_ref}>{o.text_verbatim}</span>)}
              </div>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
