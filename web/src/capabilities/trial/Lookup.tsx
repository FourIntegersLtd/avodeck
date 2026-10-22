// S25 In the witness box (docs/design/screens/S25-Lookup.dc.html). Rule 4's one exception: the search runs
// only on the sealed copy on this device, with no connection needed. The copy opens only with the PIN chosen
// when it was put here; five wrong PINs wipe it. When online, the one call made is the copy's status check,
// which wipes a copy that was removed or has ended (threat model T10); with no network the copy is kept.
// It is read-only; a copy past its end date is wiped and a copy that does not unseal is refused.
import { useEffect, useId, useMemo, useState, type CSSProperties, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { Button, Loading, Notice, TextField } from "../../foundation/ui";
import { bundleAbbr, longDate, num } from "./data";
import type { CopyContent } from "./copy";
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
        <span className="av-trial-lookup__brand"><Mark />{view?.state === "open" && view.content.case_label ? w("lookup.bar_case", { case: view.content.case_label }) : w("lookup.bar_title")}</span>
        <span className="av-trial-lookup__chip">{LOCK}{w("lookup.read_only")}</span>
        <Link className="av-trial-lookup__exit" to={`/cases/${caseId}/trial-pack`}>{w("lookup.exit")}</Link>
      </header>
      <main className="av-trial-lookup__main av-stack">
        {view ? <h1 className="av-visually-hidden">{w("lookup.title")}</h1> : <Loading />}
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
        <p className="av-trial-lookup__footer">{w("lookup.footer")}</p>
      </main>
    </div>
  );
}

const LOCK = (
  <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round">
    <rect x="5" y="10" width="14" height="10" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </svg>
);

const SEARCH = (
  <svg width="34" height="34" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round">
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-4-4" />
  </svg>
);

const PEAR = "M50 12 C66 12 74 27 76 40 C78 53 88 61 88 80 C88 101 71 114 50 114 C29 114 12 101 12 80 C12 61 22 53 24 40 C26 27 34 12 50 12 Z";

/** The Avodeck mark on its own: this screen has no way out to the website, so it is not a link. */
function Mark() {
  return (
    <svg width="31" height="40" viewBox="8 8 84 110" aria-hidden="true" focusable="false">
      <path d={PEAR} fill="#0F3D2C" />
      <path d={PEAR} transform="translate(50 76) scale(0.80) translate(-50 -76)" fill="#E4F3D6" />
      <circle cx="50" cy="83" r="17" fill="#1E6B4E" />
    </svg>
  );
}

const escape = (word: string) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The record's words with what was searched for picked out, as the court's highlighter would. */
function Highlighted({ text, query }: { text: string; query: string }) {
  const phrase = query.trim();
  const words = phrase.split(/\s+/).filter(Boolean);
  if (words.length === 0) return <>{text}</>;
  const contiguous = new RegExp(escape(phrase), "i").test(text);
  const pattern = new RegExp(`(${(contiguous ? [phrase] : words).map(escape).join("|")})`, "gi");
  const parts = text.split(pattern);
  return <>{parts.map((part, n) => (n % 2 === 1 ? <mark key={n}>{part}</mark> : part))}</>;
}

function Search({ opened }: { opened: Extract<Opened, { state: "open" }> }) {
  const id = useId();
  const [query, setQuery] = useState("");
  const { content, meta } = opened;
  const hits = useMemo(() => search(content, query, meta.locale), [content, query, meta.locale]);
  const typed = query.trim() !== "";
  return (
    <>
      <div className="av-trial-lookup__ask">
        <label className="av-trial-lookup__prompt" htmlFor={id}>{w("lookup.search")}</label>
        <div className="av-trial-lookup__box">
          {SEARCH}
          <input id={id} className="av-trial-lookup__search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} autoComplete="off" />
          {typed && hits.length > 0 && <span role="status" className="av-trial-lookup__count">{wn("lookup.results", hits.length)}</span>}
        </div>
      </div>
      {typed && hits.length === 0 && <p role="status" className="av-trial-muted">{w("lookup.nothing")}</p>}
      {typed && hits.length > 0 && (
        <ol className="av-trial-hits">
          {hits.map((hit, n) => <Result key={`${hit.kind}-${hit.ref ?? ""}-${n}`} hit={hit} bundle={content.court_bundle} locale={meta.locale} kinds={meta.kinds} sources={content.source_types ?? []} query={query} />)}
        </ol>
      )}
    </>
  );
}

function Result({ hit, bundle, locale, kinds, sources, query }: { hit: Hit; bundle: string; locale: string; kinds: Record<string, string>; sources: NonNullable<CopyContent["source_types"]>; query: string }) {
  let where = "";
  if (hit.kind === "paragraph" && hit.ref !== null) where = w("lookup.in_report", { para: hit.ref });
  if (hit.kind === "item" && hit.ref !== null) where = w("lookup.in_statement", { number: hit.ref });
  if (hit.kind === "answer" && hit.ref !== null) where = w("lookup.in_answer", { number: hit.ref });
  const kind = hit.factKind ? (kinds[hit.factKind] ?? humanise(hit.factKind)) : null;
  const source = hit.sourceType ? sources.find((s) => s.key === hit.sourceType) : undefined;
  return (
    <li className="av-trial-hit" style={source?.colour ? ({ "--av-source": source.colour } as CSSProperties) : undefined}>
      <div className="av-trial-hit__head">
        {hit.page_no !== null && hit.trial_page_no !== null && (
          <span className="av-trial-hit__tb">{w("lookup.trial_page", { abbr: bundleAbbr(bundle), number: num(hit.trial_page_no, locale) })}</span>
        )}
        {hit.page_no !== null && hit.trial_page_no === null && (
          <span className="av-trial-strong">{w("lookup.not_in_bundle", { court_bundle: bundle })}</span>
        )}
        {hit.page_no !== null && <span className="av-trial-hit__yours">{w("lookup.your_page", { page: num(hit.page_no, locale) })}</span>}
        {source && <span className="av-trial-hit__source">{source.label}</span>}
        {kind && <span className={`av-trial-hit__chip${source ? " av-trial-hit__chip--plain" : ""}`}>{kind}</span>}
      </div>
      {hit.date && <p className="av-trial-hit__title">{longDate(hit.date, locale)}</p>}
      <blockquote className="av-trial-hit__quote"><Highlighted text={hit.text} query={query} /></blockquote>
      {hit.position && <p>{w("lookup.your_position", { text: hit.position })}</p>}
      {(hit.kind === "fact" || where) && (
        <p className="av-trial-muted">{[hit.kind === "fact" ? w("lookup.as_written") : "", where].filter(Boolean).join(" ")}</p>
      )}
    </li>
  );
}
