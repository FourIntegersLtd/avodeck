// S25 In the witness box (docs/design/screens/S25-Lookup.dc.html). Rule 4's one exception: it works only
// from the sealed copy on this device, with no connection and no sign-in, and never calls the API. It is
// read-only; a copy past its end date is wiped and a copy that does not unseal is refused.
import { useEffect, useId, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { Loading, Notice } from "../../foundation/ui";
import { longDate } from "./data";
import { openCopy, type Opened } from "./offline";
import { search, type Hit } from "./search";
import "./trial.css";
import { w, wn } from "./words";

export function Lookup() {
  const { caseId = "" } = useParams();
  const [opened, setOpened] = useState<Opened | null>(null);

  useEffect(() => {
    let live = true;
    void openCopy(caseId).then((result) => live && setOpened(result));
    return () => {
      live = false;
    };
  }, [caseId]);

  return (
    <div className="av-trial-lookup">
      <header className="av-trial-lookup__bar">
        <span className="av-badge">{w("lookup.read_only")}</span>
        <Link className="av-trial-lookup__exit" to={`/cases/${caseId}/trial-pack`}>{w("lookup.exit")}</Link>
      </header>
      <main className="av-page av-stack">
        {opened ? <h1>{w("lookup.title")}</h1> : <Loading />}
        {opened?.state === "none" && <Notice><p>{w("lookup.none")}</p></Notice>}
        {opened?.state === "expired" && <Notice tone="warning"><p>{w("lookup.expired")}</p></Notice>}
        {opened?.state === "broken" && <Notice tone="error"><p>{w("lookup.broken")}</p></Notice>}
        {opened?.state === "open" && <Search opened={opened} />}
      </main>
      <footer className="av-trial-lookup__footer">
        <p>{w("lookup.footer")}</p>
      </footer>
    </div>
  );
}

function Search({ opened }: { opened: Extract<Opened, { state: "open" }> }) {
  const id = useId();
  const [query, setQuery] = useState("");
  const { content, meta } = opened;
  const hits = useMemo(() => search(content, query, meta.locale), [content, query, meta.locale]);
  const typed = query.trim() !== "";
  return (
    <>
      <div className="av-field">
        <label className="av-field__label" htmlFor={id}>{w("lookup.search")}</label>
        <input id={id} className="av-input av-trial-lookup__search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} autoComplete="off" />
      </div>
      {typed && (
        <p role="status" className="av-trial-muted">
          {hits.length === 0 ? w("lookup.nothing") : wn("lookup.results", hits.length)}
        </p>
      )}
      {typed && hits.length > 0 && (
        <ol className="av-trial-hits">
          {hits.map((hit, n) => <Result key={`${hit.kind}-${hit.ref ?? ""}-${n}`} hit={hit} bundle={content.court_bundle} locale={meta.locale} kinds={meta.kinds} />)}
        </ol>
      )}
    </>
  );
}

function Result({ hit, bundle, locale, kinds }: { hit: Hit; bundle: string; locale: string; kinds: Record<string, string> }) {
  let where = "";
  if (hit.kind === "paragraph" && hit.ref !== null) where = w("lookup.in_report", { para: hit.ref });
  if (hit.kind === "item" && hit.ref !== null) where = w("lookup.in_statement", { number: hit.ref });
  if (hit.kind === "answer" && hit.ref !== null) where = w("lookup.in_answer", { number: hit.ref });
  const kind = hit.factKind ? (kinds[hit.factKind] ?? humanise(hit.factKind)) : null;
  return (
    <li className="av-card av-stack av-trial-hit">
      <div className="av-row">
        {hit.page_no !== null && hit.trial_page_no !== null && (
          <span className="av-trial-mono av-trial-strong">{w("lookup.trial_page", { court_bundle: bundle, page: hit.trial_page_no })}</span>
        )}
        {hit.page_no !== null && hit.trial_page_no === null && (
          <span className="av-trial-strong">{w("lookup.not_in_bundle", { court_bundle: bundle })}</span>
        )}
        {hit.page_no !== null && <span className="av-trial-mono">{w("lookup.your_page", { page: hit.page_no })}</span>}
        {kind && <span className="av-badge">{kind}</span>}
        {hit.date && <span className="av-trial-mono">{longDate(hit.date, locale)}</span>}
      </div>
      <p className="av-trial-prose">{hit.text}</p>
      {hit.position && <p>{w("lookup.your_position", { text: hit.position })}</p>}
      {hit.kind === "fact" && <p className="av-trial-muted">{w("lookup.as_written")}</p>}
      {where && <p className="av-trial-muted">{where}</p>}
    </li>
  );
}
