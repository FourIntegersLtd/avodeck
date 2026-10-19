// S23 Trial page numbers (docs/design/screens/S23-TrialPages.dc.html): add the court bundle (its name from
// the pack), see every page the report relies on matched to its trial page, confirm the ones to check, and
// tell the instructing party which pages are missing. The matching runs on the server.
import { useEffect, useId, useState } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { Button, IconTile, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { joined, longDate, mediumDate, num, partyWord, pdfBody, printBlob, saveBlob, sourceLabel, tag, trialRef, vocab } from "./data";
import { PdfPicker, TrialFrame, TrialPage, TrialTitle, type Frame } from "./frame";
import { w, wn } from "./words";

type Row = Schemas["RowOut"];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const MATCH_MS = 4000;
const ERRORS = {
  precondition_failed: "trial.pages.error.stale",
  stale: "trial.pages.error.stale",
};

export function TrialPages() {
  return <TrialFrame>{(frame) => <Pages frame={frame} />}</TrialFrame>;
}

function Pages({ frame }: { frame: Frame }) {
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const data = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/concordance", { params: { path } })), [frame.caseId]);
  const [received, setReceived] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const [toldNow, setTold] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const told = toldNow || Boolean(data.data?.bundle?.missing_told_at);
  const bundleName = vocab(frame.pack, "court_bundle");
  const status = data.data?.bundle?.status;
  const matching = status === "received" || status === "matching";

  useEffect(() => {
    if (!matching) return;
    const timer = window.setInterval(data.reload, MATCH_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matching]);

  const header = (
    <TrialTitle title={w("pages.title")}>
      <p className="av-trial-lead">{w("pages.lead", { court_bundle: bundleName })}</p>
    </TrialTitle>
  );
  if (data.error) return <TrialPage width="s23">{header}<LoadError onRetry={data.reload} /></TrialPage>;
  if (!data.data) return <TrialPage width="s23">{header}<Loading /></TrialPage>;

  const { bundle, rows, counts } = data.data;
  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });

  const addBundle = async (file: File) => {
    setMessage(null);
    try {
      const body = await pdfBody(file);
      const query = received ? { received_on: received } : {};
      if (bundle) {
        await unwrap(
          api.POST("/v1/cases/{case_id}/court-bundles/{bundle_id}/supersede", {
            params: { path: { ...path, bundle_id: bundle.id }, query },
            ...(body as object),
          } as never),
        );
      } else {
        await unwrap(api.POST("/v1/cases/{case_id}/court-bundles", { params: { path, query }, ...(body as object) } as never));
      }
      setMessage({ tone: "success", text: w("pages.added") });
      data.reload();
    } catch (error) {
      fail(error);
    }
  };

  const tell = async () => {
    setMessage(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/concordance/tell-instructing-party", { params: { path } }));
      setTold(true);
    } catch (error) {
      fail(error);
    }
  };

  const exportTable = async (format: "csv" | "pdf") => {
    setMessage(null);
    try {
      const blob = await stepUp(() =>
        unwrap(api.GET("/v1/cases/{case_id}/concordance/export", { params: { path, query: { format } }, parseAs: "blob" })),
      );
      if (blob) {
        if (format === "pdf") printBlob(blob as Blob);
        else saveBlob(blob as Blob, `trial-pages.${format}`);
      }
    } catch (error) {
      fail(error);
    }
  };

  const missing = rows.filter((r) => r.status === "missing");
  const picker = (label: string) => (
    <div className="av-stack av-trial-picker-block">
      <TextField label={w("pages.received_on")} name="received_on" type="date" value={received} onChange={(e) => setReceived(e.target.value)} />
      <PdfPicker label={label} onFile={addBundle} />
    </div>
  );

  return (
    <TrialPage width="s23">
      {header}
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      {!bundle ? (
        <>
          <Notice><p>{w("pages.none", { court_bundle: bundleName })}</p></Notice>
          {frame.can("case.upload") && picker(w("pages.add", { court_bundle: bundleName }))}
        </>
      ) : (
        <>
          <div className="av-trial-top">
            <section className="av-card av-trial-bundle" data-testid="bundle-card">
              <IconTile>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2zM22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z" /></svg>
              </IconTile>
              <div>
                <p className="av-trial-strong av-trial-cap">{bundleName}</p>
                <p className="av-trial-muted">
                  {[
                    w("pages.bundle_line", { version: bundle.version, pages: num(bundle.page_count ?? 0, frame.locale), date: bundle.received_on ? longDate(bundle.received_on, frame.locale) : "" }),
                    bundle.replaces_received_on && w("pages.replaces", { date: longDate(bundle.replaces_received_on, frame.locale) }),
                  ].filter(Boolean).join(" · ")}
                </p>
                {frame.can("case.upload") && (
                  <button type="button" className="av-text-link av-trial-linkbutton" aria-expanded={replacing} onClick={() => setReplacing(true)}>{w("pages.new_version")}</button>
                )}
              </div>
            </section>
            {!matching && (
              <section className="av-card av-trial-tiles">
                <Tile id="matched" n={num(counts.matched, frame.locale)} words={wn("pages.tile_matched", counts.matched)} />
                {counts.to_confirm > 0 && <Tile id="to_confirm" n={num(counts.to_confirm, frame.locale)} words={wn("pages.tile_to_confirm", counts.to_confirm)} amber />}
                <Tile id="missing" n={num(counts.missing, frame.locale)} words={w("pages.tile_missing", { court_bundle: bundleName })} amber />
                <p className="av-trial-muted av-trial-how">{w("pages.how")}</p>
              </section>
            )}
          </div>
          {matching ? (
            <Notice><p>{w("pages.matching")}</p></Notice>
          ) : (
            <>
              {missing.length > 0 && (
                <Missing rows={missing} bundleName={bundleName} party={partyWord(frame)} canTell={frame.can("request.draft")} told={told} onTell={tell} pack={frame.pack} locale={frame.locale} />
              )}
              <Table caseId={frame.caseId} rows={rows} pack={frame.pack} locale={frame.locale} bundleName={bundleName} canConfirm={frame.can("fact.verify")} onChanged={data.reload} onCsv={() => exportTable("csv")} />
              <div className="av-trial-actions">
                <Button variant="secondary" onClick={() => exportTable("pdf")}>{w("pages.export_pdf")}</Button>
                <LinkButton to={screenPath("trial_pack", { caseId: frame.caseId }) ?? `/cases/${frame.caseId}/trial-pack`} arrow>{w("pages.to_pack")}</LinkButton>
              </div>
            </>
          )}
          {frame.can("case.upload") && replacing && picker(w("pages.replace", { court_bundle: bundleName }))}
        </>
      )}
    </TrialPage>
  );
}

function Tile({ id, n, words, amber }: { id: string; n: string; words: string; amber?: boolean }) {
  return (
    <p className={`av-trial-tile${amber ? " av-trial-tile--amber" : ""}`} data-testid={`tile-${id}`}>
      <span className="av-trial-tile__n">{n}</span>
      <span>{words}</span>
    </p>
  );
}

/** "Letters, 20 February 2024": what the page is, from the pack's word for its source type and its date. */
function documentOf(row: Row, pack: Schemas["CasePack"], locale: string): string {
  return [sourceLabel(pack, row.source_type), row.document_date ? mediumDate(row.document_date, locale) : ""].filter(Boolean).join(", ");
}

function Missing({ rows, bundleName, party, canTell, told, onTell, pack, locale }: { rows: Row[]; bundleName: string; party: string; canTell: boolean; told: boolean; onTell: () => void; pack: Schemas["CasePack"]; locale: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="av-card av-stack av-trial-missing">
      <div className="av-trial-missing__head">
        <IconTile tone="amber">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.5" /></svg>
        </IconTile>
        <h2 id={id}>{wn("pages.missing_heading", rows.length, { court_bundle: bundleName })}</h2>
        {canTell && !told && <Button variant="secondary" onClick={onTell}>{w("pages.tell", { party })}</Button>}
      </div>
      <ul className="av-trial-list av-trial-list--plain">
        {rows.map((r) => {
          const document = documentOf(r, pack, locale);
          const paras = joined(r.paragraphs, locale);
          const count = r.paragraphs.length;
          return <li key={r.id}>{document ? wn("pages.missing_row_doc", count, { page: num(r.page_no, locale), document, paras }) : wn("pages.missing_row", count, { page: num(r.page_no, locale), paras })}</li>;
        })}
      </ul>
      {told && <Notice tone="success"><p>{w("pages.told")}</p></Notice>}
    </section>
  );
}

const STATUS_WORD: Record<string, "pages.status.matched" | "pages.status.to_confirm" | "pages.status.missing"> = {
  matched: "pages.status.matched",
  to_confirm: "pages.status.to_confirm",
  missing: "pages.status.missing",
};

const SHOWN = 4;

function Table({ caseId, rows, pack, locale, bundleName, canConfirm, onChanged, onCsv }: { caseId: string; rows: Row[]; pack: Schemas["CasePack"]; locale: string; bundleName: string; canConfirm: boolean; onChanged: () => void; onCsv: () => void }) {
  const [all, setAll] = useState(false);
  // The ones to check always show; the rest fill the first few places, then "and N more".
  let room = SHOWN;
  const shown = all ? rows : rows.filter((r) => (r.status === "to_confirm" && !r.confirmed) || room-- > 0);
  const hidden = rows.length - shown.length;
  return (
    <section className="av-card av-trial-table-wrap">
      <table className="av-trial-table">
        <thead>
          <tr>
            <th scope="col">{w("pages.col.paragraph")}</th>
            <th scope="col">{w("pages.col.yours")}</th>
            <th scope="col"><span className="av-visually-hidden">{w("pages.col.to")}</span></th>
            <th scope="col" className="av-trial-cap">{w("pages.col.trial", { court_bundle: bundleName })}</th>
            <th scope="col">{w("pages.col.document")}</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => <TableRow key={r.id} caseId={caseId} row={r} document={documentOf(r, pack, locale)} bundleName={bundleName} locale={locale} canConfirm={canConfirm} onChanged={onChanged} />)}
        </tbody>
      </table>
      <p className="av-trial-muted av-trial-foot">
        {hidden > 0 && (
          <>
            {w("pages.and_more", { count: hidden })} <button type="button" className="av-text-link av-trial-linkbutton" onClick={() => setAll(true)}>{w("pages.show_all")}</button>{" · "}
          </>
        )}
        <button type="button" className="av-text-link av-trial-linkbutton" onClick={onCsv}>{w("pages.export_csv")}</button>
      </p>
    </section>
  );
}

function TableRow({ caseId, row, document, bundleName, locale, canConfirm, onChanged }: { caseId: string; row: Row; document: string; bundleName: string; locale: string; canConfirm: boolean; onChanged: () => void }) {
  const [value, setValue] = useState(row.trial_page_no === null ? "" : String(row.trial_page_no));
  const [state, setState] = useState<Message>(null);
  const confirmable = canConfirm && row.status === "to_confirm" && !row.confirmed;

  const confirm = async () => {
    setState(null);
    try {
      await unwrap(
        api.PATCH("/v1/cases/{case_id}/concordance/{row_id}", {
          params: { path: { case_id: caseId, row_id: row.id } },
          headers: ifMatch(tag(row.version)),
          body: { trial_page_no: Number(value) },
        }),
      );
      setState({ tone: "success", text: w("pages.confirmed") });
      onChanged();
    } catch (error) {
      setState({ tone: "error", text: errorMessage(error, { ...ERRORS, invalid: "trial.pages.error.invalid", validation_error: "trial.pages.error.invalid" }) });
    }
  };

  const status = row.confirmed ? w("pages.status.confirmed") : row.status === "matched" ? "" : STATUS_WORD[row.status] ? w(STATUS_WORD[row.status]!, { court_bundle: bundleName }) : "";
  return (
    <tr>
      <td>{row.paragraphs.join(", ")}</td>
      <td className="av-trial-mono">{w("pages.page_ref", { page: num(row.page_no, locale) })}</td>
      <td aria-hidden="true" className="av-trial-arrow">→</td>
      <td className="av-trial-mono av-trial-tb">
        {confirmable ? (
          <div className="av-row">
            <input
              type="number"
              className="av-input av-trial-page-input"
              aria-label={w("pages.trial_input", { page: row.page_no })}
              value={value}
              min={1}
              onChange={(e) => setValue(e.target.value)}
            />
            <Button variant="secondary" onClick={confirm} disabled={!value}>{w("pages.confirm")}</Button>
          </div>
        ) : (
          row.trial_page_no === null ? "" : trialRef(bundleName, row.trial_page_no, locale)
        )}
        {state && <p className={state.tone === "error" ? "av-field__error" : "av-trial-muted"} role="status">{state.text}</p>}
      </td>
      <td>
        {document}
        {status && <span className={`av-badge${row.confirmed ? " av-badge--checked" : row.status === "to_confirm" ? " av-badge--please_check" : ""}`}>{status}</span>}
      </td>
    </tr>
  );
}
