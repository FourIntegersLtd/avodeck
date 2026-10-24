// The support console (permissions.md "Avodeck staff"): find a case and see its details and grant status.
// Staff only: the server answers anyone else with 404, which shows the not found page. It shows case details
// only; records open through the ordinary case screens, and only while the expert's grant is on.
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, TextField } from "../../foundation/ui";
import { MessageNotice, NotFoundPage, OWN_ERRORS, type Message } from "./common";
import { flag } from "./flags";
import { w } from "./words";

type Found = Schemas["SupportCase"];

// The custody-transfer route is not in the generated client yet; its path is typed as a sibling POST route's.
const CUSTODY = "/v1/support/cases/{case_id}/custody" as "/v1/cases/{case_id}/named-expert";

function statusWords(status: string): string {
  return t(`organisations.console.status.${status}`, {}, humanise(status));
}

function when(iso: string, locale: string | null | undefined): string {
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export function SupportConsole() {
  const { me } = useSession();
  const stepUp = useStepUp();
  const [query, setQuery] = useState("");
  const [cases, setCases] = useState<Found[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  if (hidden) return <NotFoundPage />;
  const search = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setMessage(null);
    try {
      const found = await stepUp(() => unwrap(api.GET("/v1/support/cases", { params: { query: { q: query.trim() } } })));
      if (found) setCases(found.cases);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return setHidden(true);
      setMessage({ tone: "error", text: errorMessage(err, { ...OWN_ERRORS, server_error: "organisations.console.error" }) });
    }
  };
  const served = (cases ?? []).filter((c) => c.served);
  return (
    <div className="av-page av-page--narrow av-stack">
      <h1>{w("console.title")}</h1>
      <p>{w("console.intro")}</p>
      <form className="av-row av-org-search" onSubmit={(e) => void search(e)} noValidate>
        <TextField label={w("console.query")} name="q" autoComplete="off" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit">{w("console.search")}</Button>
      </form>
      <MessageNotice message={message} />
      {cases && cases.length === 0 && <p className="av-muted">{w("console.none")}</p>}
      {cases && cases.length > 0 && (
        <ul className="av-rows av-org-results" role="list" aria-label={w("console.results")}>
          {cases.map((c) => (
            <li key={c.case_id} className="av-row-item av-card av-org-result">
              <div className="av-row-item__text">
                <p className="av-field__label">{c.case_id}</p>
                <p className="av-muted">{`${w("console.status")}: ${statusWords(c.status)} · ${c.organisation_kind === "personal" ? w("console.personal") : w("console.team")} · ${c.served ? w("console.served") : w("console.not_served")}`}</p>
                <p>{c.grant_active && c.grant_expires_at ? w("console.grant_active", { when: when(c.grant_expires_at, me.person.locale) }) : w("console.grant_none")}</p>
                {!c.grant_active && <p className="av-muted av-small">{w("console.records_locked")}</p>}
              </div>
              {c.grant_active && (
                <Link className="av-button av-button--secondary" to={`/cases/${c.case_id}`}>
                  {w("console.open_case")}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
      {served.length > 0 && flag("support_custody_transfer") && <Custody cases={served.map((c) => c.case_id)} />}
    </div>
  );
}

function Custody({ cases }: { cases: string[] }) {
  const stepUp = useStepUp();
  const [caseId, setCaseId] = useState(cases[0] ?? "");
  const [to, setTo] = useState("custodian");
  const [evidence, setEvidence] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setMessage(null);
    try {
      const done = await stepUp(() => unwrap(api.POST(CUSTODY, { params: { path: { case_id: caseId } }, body: { to, evidence } as never })));
      if (done !== undefined) setMessage({ tone: "success", text: w("console.custody.done") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
    }
  };
  return (
    <section aria-labelledby="custody-title" className="av-card av-org-card">
      <h2 id="custody-title">{w("console.custody.title")}</h2>
      <p>{w("console.custody.intro")}</p>
      <MessageNotice message={message} />
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <div className="av-field">
          <label className="av-field__label" htmlFor="custody-case">
            {w("console.custody.case")}
          </label>
          <select id="custody-case" className="av-input av-org-select" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
            {cases.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div className="av-field">
          <label className="av-field__label" htmlFor="custody-to">
            {w("console.custody.to")}
          </label>
          <select id="custody-to" className="av-input av-org-select" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="custodian">{w("console.custody.to_custodian")}</option>
            <option value="organisation">{w("console.custody.to_org")}</option>
          </select>
        </div>
        <TextField label={w("console.custody.evidence")} name="evidence" autoComplete="off" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
        <Button type="submit">{w("console.custody.button")}</Button>
      </form>
    </section>
  );
}
