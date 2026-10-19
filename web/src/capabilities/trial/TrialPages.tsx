// S23 Trial page numbers (docs/design/screens/S23-TrialPages.dc.html): add the court bundle (its name from
// the pack), see every page the report relies on matched to its trial page, confirm the ones to check, and
// tell the instructing party which pages are missing. The matching runs on the server.
import { useEffect, useId, useState } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { longDate, pdfBody, saveBlob, tag, vocab } from "./data";
import { PdfPicker, TrialFrame, TrialHeader, type Frame } from "./frame";
import { w, wn } from "./words";

type Row = Schemas["RowOut"];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const SCREEN = "court_bundle_pages";
const MATCH_MS = 4000;
const ERRORS = {
  precondition_failed: "trial.pages.error.stale",
  stale: "trial.pages.error.stale",
};

export function TrialPages() {
  return <TrialFrame>{(frame) => <Pages frame={frame} />}</TrialFrame>;
}

/** The instructing party's word from the pack, by the case's instructing party type. */
function partyWord(frame: Frame): string {
  const type = (frame.item as { instructing_party_type?: string | null }).instructing_party_type;
  const found = frame.pack.instructing_party_types.find((p) => p.key === type);
  return found?.label ?? w("pages.tell_fallback");
}

function Pages({ frame }: { frame: Frame }) {
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const data = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/concordance", { params: { path } })), [frame.caseId]);
  const [received, setReceived] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const [told, setTold] = useState(false);
  const bundleName = vocab(frame.pack, "court_bundle");
  const status = data.data?.bundle?.status;
  const matching = status === "received" || status === "matching";

  useEffect(() => {
    if (!matching) return;
    const timer = window.setInterval(data.reload, MATCH_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matching]);

  const header = <TrialHeader pack={frame.pack} screen={SCREEN} title={w("pages.title")} />;
  if (data.error) return <div className="av-page av-stack">{header}<LoadError onRetry={data.reload} /></div>;
  if (!data.data) return <div className="av-page av-stack">{header}<Loading /></div>;

  const { bundle, rows, counts } = data.data;
  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });

  const addBundle = async (file: File) => {
    setMessage(null);
    try {
      const body = await pdfBody(file);
      await unwrap(
        api.POST("/v1/cases/{case_id}/court-bundles", {
          params: { path, query: received ? { received_on: received } : {} },
          ...(body as object),
        } as never),
      );
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
      if (blob) saveBlob(blob as Blob, `trial-pages.${format}`);
    } catch (error) {
      fail(error);
    }
  };

  const missing = rows.filter((r) => r.status === "missing");
  const picker = (label: string) => (
    <div className="av-stack">
      <TextField label={w("pages.received_on")} name="received_on" type="date" value={received} onChange={(e) => setReceived(e.target.value)} />
      <PdfPicker label={label} onFile={addBundle} />
    </div>
  );

  return (
    <div className="av-page av-stack">
      {header}
      <p>{w("pages.lead", { court_bundle: bundleName })}</p>
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      {!bundle ? (
        <>
          <Notice><p>{w("pages.none", { court_bundle: bundleName })}</p></Notice>
          {frame.can("case.upload") && picker(w("pages.add", { court_bundle: bundleName }))}
        </>
      ) : (
        <>
          <p className="av-trial-muted">
            {w("pages.bundle_line", { version: bundle.version, pages: bundle.page_count ?? 0, date: bundle.received_on ? longDate(bundle.received_on, frame.locale) : "" })}
          </p>
          {matching ? (
            <Notice><p>{w("pages.matching")}</p></Notice>
          ) : (
            <>
              <ul className="av-trial-counts">
                <li>{wn("pages.matched", counts.matched)}</li>
                <li>{wn("pages.to_confirm", counts.to_confirm)}</li>
              </ul>
              <p className="av-trial-muted">{w("pages.how")}</p>
              {missing.length > 0 && (
                <Missing rows={missing} bundleName={bundleName} party={partyWord(frame)} canTell={frame.can("request.draft")} told={told} onTell={tell} />
              )}
              <Table caseId={frame.caseId} rows={rows} bundleName={bundleName} canConfirm={frame.can("fact.verify")} onChanged={data.reload} />
              <div className="av-trial-actions">
                <Button variant="secondary" onClick={() => exportTable("pdf")}>{w("pages.export_pdf")}</Button>
                <Button variant="secondary" onClick={() => exportTable("csv")}>{w("pages.export_csv")}</Button>
              </div>
            </>
          )}
          {frame.can("case.upload") && picker(w("pages.replace", { court_bundle: bundleName }))}
        </>
      )}
    </div>
  );
}

function Missing({ rows, bundleName, party, canTell, told, onTell }: { rows: Row[]; bundleName: string; party: string; canTell: boolean; told: boolean; onTell: () => void }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="av-card av-stack av-trial-missing">
      <h2 id={id}>{wn("pages.missing_heading", rows.length, { court_bundle: bundleName })}</h2>
      <ul className="av-trial-list">
        {rows.map((r) => <li key={r.id}>{w("pages.missing_row", { page: r.page_no, paras: r.paragraphs.join(", ") })}</li>)}
      </ul>
      {told && <Notice tone="success"><p>{w("pages.told")}</p></Notice>}
      {canTell && !told && (
        <div className="av-trial-actions">
          <Button variant="secondary" onClick={onTell}>{w("pages.tell", { party })}</Button>
        </div>
      )}
    </section>
  );
}

const STATUS_WORD: Record<string, "pages.status.matched" | "pages.status.to_confirm" | "pages.status.missing"> = {
  matched: "pages.status.matched",
  to_confirm: "pages.status.to_confirm",
  missing: "pages.status.missing",
};

function Table({ caseId, rows, bundleName, canConfirm, onChanged }: { caseId: string; rows: Row[]; bundleName: string; canConfirm: boolean; onChanged: () => void }) {
  return (
    <div className="av-trial-table-wrap">
      <table className="av-trial-table">
        <thead>
          <tr>
            <th scope="col">{w("pages.col.paragraph")}</th>
            <th scope="col">{w("pages.col.yours")}</th>
            <th scope="col">{w("pages.col.trial", { court_bundle: bundleName })}</th>
            <th scope="col">{w("pages.col.status")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => <TableRow key={r.id} caseId={caseId} row={r} bundleName={bundleName} canConfirm={canConfirm} onChanged={onChanged} />)}
        </tbody>
      </table>
    </div>
  );
}

function TableRow({ caseId, row, bundleName, canConfirm, onChanged }: { caseId: string; row: Row; bundleName: string; canConfirm: boolean; onChanged: () => void }) {
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

  const status = row.confirmed ? w("pages.status.confirmed") : STATUS_WORD[row.status] ? w(STATUS_WORD[row.status]!, { court_bundle: bundleName }) : "";
  return (
    <tr>
      <td className="av-trial-mono">{row.paragraphs.join(", ")}</td>
      <td className="av-trial-mono">{w("pages.page_ref", { page: row.page_no })}</td>
      <td className="av-trial-mono">
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
          row.trial_page_no ?? ""
        )}
        {state && <p className={state.tone === "error" ? "av-field__error" : "av-trial-muted"} role="status">{state.text}</p>}
      </td>
      <td>
        <span className={`av-badge${row.confirmed ? " av-badge--checked" : row.status === "to_confirm" ? " av-badge--please_check" : ""}`}>{status}</span>
      </td>
    </tr>
  );
}
