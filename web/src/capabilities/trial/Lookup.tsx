// S25 In the witness box (docs/design/screens/S25-Lookup.dc.html). Rule 4's one exception: the search runs
// only on the sealed copy on this device, with no connection needed. The copy opens only with the PIN chosen
// when it was put here; five wrong PINs wipe it. When online, the one call made is the copy's status check,
// which wipes a copy that was removed or has ended (threat model T10); with no network the copy is kept.
// It is read-only; a copy past its end date is wiped and a copy that does not unseal is refused.
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { Button, Loading, Notice, TextField } from "../../foundation/ui";
import { longDate } from "./data";
import { checkWithServer, findCopy, unlockCopy, type Opened } from "./offline";
import { search, type Hit } from "./search";
import "./trial.css";
import { w, wn } from "./words";

type View =
  | { state: "none" }
  | { state: "expired" }
  | { state: "revoked" }
  | { state: "locked"; wrong: number | null }
  | Exclude<Opened, { state: "wrong" }>;

export function Lookup() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<View | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void (async () => {
      const found = await findCopy(caseId);
      if (found.state !== "locked") return live && setView(found);
      const checked = await checkWithServer(caseId, found.packageId);
      if (live) setView(checked === "wiped" ? { state: "revoked" } : { state: "locked", wrong: null });
    })();
    return () => {
      live = false;
    };
  }, [caseId]);

  const open = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await unlockCopy(caseId, pin);
      setPin("");
      setView(result.state === "wrong" ? { state: "locked", wrong: result.triesLeft } : result);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="av-trial-lookup">
      <header className="av-trial-lookup__bar">
        <span className="av-badge">{w("lookup.read_only")}</span>
        <Link className="av-trial-lookup__exit" to={`/cases/${caseId}/trial-pack`}>{w("lookup.exit")}</Link>
      </header>
      <main className="av-page av-stack">
        {view ? <h1>{w("lookup.title")}</h1> : <Loading />}
        {view?.state === "none" && <Notice><p>{w("lookup.none")}</p></Notice>}
        {view?.state === "expired" && <Notice tone="warning"><p>{w("lookup.expired")}</p></Notice>}
        {view?.state === "revoked" && <Notice tone="warning"><p>{w("lookup.revoked")}</p></Notice>}
        {view?.state === "wiped" && <Notice tone="error"><p>{w("lookup.pin.wiped")}</p></Notice>}
        {view?.state === "broken" && <Notice tone="error"><p>{w("lookup.broken")}</p></Notice>}
        {view?.state === "locked" && (
          <form className="av-stack" onSubmit={open}>
            <TextField
              label={w("lookup.pin.label")}
              name="pin"
              type="password"
              inputMode="numeric"
              autoComplete="off"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              error={view.wrong === null ? undefined : wn("lookup.pin.wrong", view.wrong)}
            />
            <div className="av-trial-actions">
              <Button type="submit" disabled={busy || pin === ""}>{w("lookup.pin.open")}</Button>
            </div>
            {busy && <p>{w("lookup.opening")}</p>}
          </form>
        )}
        {view?.state === "open" && <Search opened={view} />}
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
