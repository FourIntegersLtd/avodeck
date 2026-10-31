// S16 Your report (docs/design/screens/S16-Report.dc.html): the numbered sections at the left (headings from the
// pack's labels), the chosen section as a paper page in the middle, what the court requires and the methodology
// note at the right. The server decides every change; edits send the version this screen saw.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { screenPath } from "../../foundation/lib/screens";
import { errorAction, errorMessage, type ErrorAction } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, apiOrigin, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBox, LinkButton, LoadError, Loading, Notice, SegmentedControl, StaleChoice, Updating, useBusy } from "../../foundation/ui";
import { downloadPath, useLocale, useSectionName, useWaiting, versionTag } from "./shared";
import { w } from "./words";

type Report = Schemas["ReportOut"];
const STYLES = ["page", "paragraph"] as const;
const ERRORS = { stale: "report.stale", not_built: "report.not_built" };

export function ReportScreen() {
  const { caseId = "" } = useParams();
  const { item, pack } = useCaseFrame();
  const locale = useLocale();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Report#ReportScreen:found" });
  const settings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/settings", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Report#ReportScreen:settings" });
  const wait = useWaiting(found.reload, found.data?.version?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [styleVersion, setStyleVersion] = useState<number | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  const { busy: styling, run: guard } = useBusy();

  if (found.error || settings.error) return <div className="av-page"><LoadError onRetry={() => { found.reload(); settings.reload(); }} /></div>;
  if (found.data === undefined || !settings.data) return <div className="av-page"><Loading /></div>;
  const data = (found.data ?? { version: null, versions: [], sections: [], checklist: { items: [], done: 0, total: 0 }, locked: false }) as unknown as Report;
  const version = data.version;
  const currentStyle = style ?? settings.data.citation_style;
  const currentVersion = styleVersion ?? settings.data.version;
  const selected = data.sections.find((s) => s.key === chosen) ?? data.sections[0] ?? null;

  const attempt = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (err) {
      if (err instanceof ApiError && err.code === "served_use_supplementary") setRefused(true);
      else setError(errorMessage(err, ERRORS));
    }
  };

  const build = () =>
    attempt(async () => {
      await unwrap(api.POST("/v1/cases/{case_id}/report/build", { params: { path: { case_id: caseId } } }));
      wait.start();
    });

  const changeStyle = (next: string) =>
    attempt(async () => {
      if (next !== "page" && next !== "paragraph") return;
      const saved = await unwrap(
        api.PATCH("/v1/cases/{case_id}/report/settings", {
          params: { path: { case_id: caseId } },
          body: { citation_style: next },
          headers: ifMatch(versionTag(currentVersion)),
        }),
      );
      setStyle(saved.citation_style);
      setStyleVersion(saved.version);
      if (saved.job_id) wait.start();
    });

  // Rule 8: once the main report is served (or withdrawn) it is locked; changes go in a supplementary report.
  const served = refused || data.locked;
  const appendices = ((pack.report_appendices ?? []) as Array<{ key: string; label?: string }>).map((a) => a.label ?? humanise(a.key));
  const limits = ((pack.compliance_checklist as unknown as { page_limit?: Record<string, number> } | undefined)?.page_limit ?? {}) as Record<string, number>;
  const limit = item.court_track ? limits[item.court_track] : undefined;
  const limitWords = limit ? w("report.limit", { count: limit }) : w("report.no_limit");
  const footer = [
    version ? (version.page_count ? w("report.version", { name: version.display_name, pages: version.page_count }) : w("report.version_nopages", { name: version.display_name })) : null,
    version && item.court_track ? (data.court_track_label ? w("report.track_limit", { track: data.court_track_label, limit: limitWords }) : limitWords) : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="av-page rp-page rp-page--wide">
      <div className="rp-head">
        <div>
          <h1 className="rp-title">{w("report.title")}</h1>
          <p className="rp-lead">{w("report.intro")}</p>
        </div>
        <div className="rp-head__tools">
          <SegmentedControl label={w("report.style")} value={currentStyle} onChange={(next) => { void guard(() => changeStyle(next)); }} options={STYLES.map((key) => ({ value: key, label: w(`report.style.${key}`), disabled: styling }))} />
          <span className="av-visually-hidden">{w("report.style_changed")}</span>
          {version && <a className="av-button av-button--quiet" href={`${apiOrigin()}${downloadPath(caseId, version.id, "docx")}`}>{w("report.open_word")}</a>}
        </div>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {served && (
        <Notice tone="amber">
          <p>{w("report.served")}</p>
          <LinkButton to={screenPath("S21b", { caseId }) ?? `/cases/${caseId}`}>{w("report.served_link")}</LinkButton>
        </Notice>
      )}
      {wait.waiting && <Notice tone="mint"><p>{w("building")}</p></Notice>}
      {!version && <Notice tone="mint"><p>{w("report.empty")}</p></Notice>}
      {version?.template_fallback && <Notice tone="amber"><p>{w("report.fallback")}</p></Notice>}

      <div className="rp-report">
        <Contents sections={data.sections} selected={selected?.key ?? null} onChoose={setChosen} appendices={appendices} />
        {selected ? <Paper key={selected.key} caseId={caseId} data={data} section={selected} number={data.sections.indexOf(selected) + 1} onSaved={found.reload} /> : <div />}
        <aside className="rp-side">
          <section className="rp-card" aria-labelledby="checklist-title">
            <h2 id="checklist-title" className="rp-card__title">{w("report.checklist")}</h2>
            <ul className="rp-needs">
              {data.checklist.items.map((entry) => (
                <li key={entry.key} className={`rp-need${entry.done ? "" : " rp-need--todo"}`}>
                  {entry.done ? <TickIcon /> : <RingIcon />}
                  <span className="av-visually-hidden">{entry.done ? w("report.checklist_item_done") : w("report.checklist_item_todo")}</span>
                  <span>{entry.text ?? humanise(entry.key)}</span>
                </li>
              ))}
            </ul>
            <p className="av-muted rp-small">{w("report.checklist_done", { done: data.checklist.done, total: data.checklist.total })}</p>
          </section>
          <Methodology caseId={caseId} locale={locale} />
        </aside>
      </div>

      <div className="rp-bar">
        <span className="av-muted rp-bar__note">{footer}</span>
        <div className="rp-bar__actions">
          {!served ? (version ? (
            <Button variant="quiet" onClick={() => build()} disabled={wait.waiting}>{w("report.rebuild")}</Button>
          ) : (
            <Button onClick={() => build()} disabled={wait.waiting}>{w("report.build")}</Button>
          )) : null}
          {version && <LinkButton arrow to={`/cases/${caseId}/final-check`}>{w("report.final_check")}</LinkButton>}
        </div>
      </div>
    </div>
  );
}

function TickIcon() {
  return (
    <svg className="rp-tick" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

function RingIcon() {
  return (
    <svg className="rp-ring" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5">
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

function Contents({ sections, selected, onChoose, appendices }: { sections: Report["sections"]; selected: string | null; onChoose: (key: string) => void; appendices: string[] }) {
  const nameOf = useSectionName();
  return (
    <nav className="rp-contents" aria-label={w("report.sections")}>
      <ol className="rp-contents__list" aria-label={w("report.contents")}>
        {sections.map((section, index) => {
          const name = nameOf(section.key);
          const isDone = section.written || section.generated;
          const partway = section.total != null && section.done != null && section.done < section.total;
          return (
            <li key={section.key} aria-label={name} className={`rp-contents__row${section.key === selected ? " rp-contents__row--on" : ""}`}>
              <span className="rp-contents__no" aria-hidden="true">{index + 1}</span>{" "}
              <button type="button" className="rp-contents__name" aria-current={section.key === selected ? "true" : undefined} onClick={() => onChoose(section.key)}>{name}</button>
              {partway ? (
                <span className="rp-contents__progress">{w("report.progress", { done: section.done ?? 0, total: section.total ?? 0 })}</span>
              ) : isDone ? (
                <>
                  <TickIcon />
                  <span className="av-visually-hidden">{w("report.section.done")}</span>
                </>
              ) : (
                <span className="rp-contents__todo">{w("report.section.todo")}</span>
              )}
            </li>
          );
        })}
      </ol>
      {appendices.length > 0 && <p className="rp-contents__appendices">{w("report.appendices", { names: appendices.join(", ") })}</p>}
    </nav>
  );
}

function Paper({ caseId, data, section, number, onSaved }: { caseId: string; data: Report; section: Report["sections"][number]; number: number; onSaved: () => void }) {
  const nameOf = useSectionName();
  const [editing, setEditing] = useState(false);
  const name = nameOf(section.key);
  // No built report yet answers 404: that is the empty section, not a failure.
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/paragraphs", { params: { path: { case_id: caseId }, query: { section: section.key } } })).catch((err: unknown) => { if (err instanceof ApiError && err.status === 404) return { paragraphs: [] as Schemas["ParagraphOut"][] }; throw err; }), [caseId, section.key, data.version?.id], { cache: "report/Report#Paper:found" });
  const updating = found.loading && !!found.data;
  return (
    <div className="rp-centre">
      <section className="rp-paper av-busy-dim" aria-label={w("report.paper")} aria-live="polite" aria-busy={updating}>
        {updating && <Updating label={w("report.updating")} />}
        <h2 className="rp-paper__heading">{`${number}. ${name}`}</h2>
        {found.error ? <LoadError onRetry={found.reload} /> : !found.data ? <Loading inline /> : found.data.paragraphs.length === 0 ? (
          <p className="rp-paper__empty">{w("report.section.empty")}</p>
        ) : (
          found.data.paragraphs.map((p) => (
            <div key={p.para_no} className="rp-para">
              <span className="rp-para__no">{`${number}.${p.para_no}`}</span>
              <span>{p.text}</span>
            </div>
          ))
        )}
      </section>
      <div className="rp-under">
        <span className="av-badge">{section.written ? w("report.section.written") : section.generated ? w("report.section.generated") : w("report.section.todo")}</span>
        <Button variant="secondary" aria-label={`${section.written ? w("report.section.edit") : w("report.section.write")} ${name}`} onClick={() => setEditing(!editing)}>
          {section.written ? w("report.section.edit") : w("report.section.write")}
        </Button>
      </div>
      {editing && <SectionEditor key={section.key} caseId={caseId} sectionKey={section.key} onClose={() => setEditing(false)} onSaved={onSaved} />}
      {data.version && <Preview caseId={caseId} pages={data.version.page_count ?? 1} />}
    </div>
  );
}

function SectionEditor({ caseId, sectionKey, onClose, onSaved }: { caseId: string; sectionKey: string; onClose: () => void; onSaved: () => void }) {
  // A section never saved answers 404: start an empty one (version 0; the first save sends no If-Match).
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/sections/{key}", { params: { path: { case_id: caseId, key: sectionKey } } })).catch((err: unknown): Schemas["SectionText"] => { if (err instanceof ApiError && err.status === 404) return { key: sectionKey, body: "", citations: [], version: 0 }; throw err; }), [caseId, sectionKey]);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading inline />;
  return <SectionForm caseId={caseId} initial={found.data} onClose={onClose} onSaved={onSaved} />;
}

const MARKER = /\[\[cite:(\d+)\]\]/g;
const SHOWN_MARKER = /⟦(\d+)⟧/g;
type Cite = Schemas["Citation"];
type Saved = { body: string; citations: Cite[]; version: number };
/** The text as the box shows it: each citation marker is one whole chip token, never raw marker syntax. */
const toView = (body: string) => body.replace(MARKER, (_, n: string) => `⟦${n}⟧`);

/** Drop citations whose marker is gone from the text and renumber the rest in order (the server wants 1..n). */
function prune(body: string, refs: Cite[]): { body: string; refs: Cite[] } {
  const used = new Set([...body.matchAll(MARKER)].map((m) => Number(m[1])));
  const kept: Cite[] = [];
  const renumber = new Map<number, number>();
  refs.forEach((r, i) => {
    if (used.has(i + 1)) {
      kept.push(r);
      renumber.set(i + 1, kept.length);
    }
  });
  if (kept.length === refs.length) return { body, refs };
  return { body: body.replace(MARKER, (whole, n: string) => (renumber.has(Number(n)) ? `[[cite:${renumber.get(Number(n))}]]` : whole)), refs: kept };
}

/** What the box now holds, turned back into text with markers; null when typing would break a chip. */
function fromView(next: string, body: string, refs: Cite[]): { body: string; refs: Cite[] } | null {
  const tokens = [...next.matchAll(SHOWN_MARKER)].map((m) => Number(m[1]));
  if (next.replace(SHOWN_MARKER, "").match(/[⟦⟧]/)) return null;
  const had = new Set([...body.matchAll(MARKER)].map((m) => Number(m[1])));
  if (new Set(tokens).size !== tokens.length || tokens.some((n) => !had.has(n))) return null;
  return prune(next.replace(SHOWN_MARKER, (_, n: string) => `[[cite:${n}]]`), refs);
}

function SectionForm({ caseId, initial, onClose, onSaved }: { caseId: string; initial: Schemas["SectionText"]; onClose: () => void; onSaved: () => void }) {
  const [body, setBody] = useState(initial.body);
  const [refs, setRefs] = useState<Cite[]>(initial.citations);
  const [version, setVersion] = useState(initial.version);
  const [problem, setProblem] = useState<{ message: string; action: ErrorAction | null; cite: number | null } | null>(null);
  const [stale, setStale] = useState<Saved | null | "unknown">(null);
  const [saved, setSaved] = useState(false);
  const nameOf = useSectionName();
  const factLink = (n: number) => `/cases/${caseId}/consultation/${refs[n - 1]?.fact_id ?? ""}`;
  const save = async (at: number = version) => {
    setProblem(null);
    setSaved(false);
    try {
      const next = await unwrap(
        api.PUT("/v1/cases/{case_id}/report/sections/{key}", {
          params: { path: { case_id: caseId, key: initial.key } },
          body: { body, citations: refs },
          headers: at > 0 ? ifMatch(versionTag(at)) : undefined,
        }),
      );
      setVersion(next.version);
      setSaved(true);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        const latest = await unwrap(api.GET("/v1/cases/{case_id}/report/sections/{key}", { params: { path: { case_id: caseId, key: initial.key } } })).catch(() => null);
        setStale(latest ? { body: latest.body, citations: latest.citations, version: latest.version } : "unknown");
      } else if (err instanceof ApiError && err.code === "not_citable") {
        const n = Number(err.extra.citation ?? 0);
        setProblem({ message: n > 0 ? w("report.section.cite_refused", { number: n }) : w("report.section.cite_invalid"), action: null, cite: n > 0 ? n : null });
      } else if (err instanceof ApiError && err.status === 422) {
        setProblem({ message: w("report.section.cite_invalid"), action: null, cite: null });
      } else {
        setProblem({ message: errorMessage(err, ERRORS), action: errorAction(err, caseId), cite: null });
      }
    }
  };
  const edit = (shown: string) => {
    const next = fromView(shown, body, refs);
    if (!next) return;
    setBody(next.body);
    setRefs(next.refs);
    setSaved(false);
  };
  return (
    <div className="av-stack">
      {problem && (
        <Notice tone="error">
          <p>{problem.message}</p>
          {problem.cite !== null && <Link className="av-text-link" to={factLink(problem.cite)}>{w("report.section.cite_open", { number: problem.cite })}</Link>}
          {problem.action && <LinkButton to={problem.action.to}>{problem.action.label}</LinkButton>}
        </Notice>
      )}
      <DictationBox caseId={caseId} context="report_section" label={w("report.section.label", { name: nameOf(initial.key) })} value={toView(body)} onChange={edit} rows={10} />
      {refs.length > 0 && (
        <ul className="av-stack">
          {refs.map((r, i) => (
            <li key={`${r.fact_id}-${i}`}>
              <Link className="av-text-link" to={factLink(i + 1)} aria-label={w("report.section.cite_open", { number: i + 1 })}>{`⟦${i + 1}⟧ ${r.quote_verbatim}`}</Link>
            </li>
          ))}
        </ul>
      )}
      <div className="av-row">
        <Button variant="secondary" onClick={() => save()}>{w("report.section.save")}</Button>
        <Button variant="secondary" onClick={onClose}>{w("report.section.close")}</Button>
        {saved && <span role="status">{w("report.section.saved")}</span>}
      </div>
      {stale && (
        <StaleChoice
          draft={toView(body)}
          saved={stale === "unknown" ? null : toView(stale.body)}
          savedVersion={stale === "unknown" ? 0 : stale.version}
          onKeepMine={(v) => { setStale(null); void save(v); }}
          onUseSaved={(_, v) => {
            if (stale !== "unknown") { setBody(stale.body); setRefs(stale.citations); setVersion(v); }
            setStale(null);
          }}
          onClose={() => setStale(null)}
          words={{ title: w("report.section.stale_title"), keepMine: w("report.section.stale_yours"), useSaved: w("report.section.stale_saved") }}
        />
      )}
    </div>
  );
}

function Methodology({ caseId, locale }: { caseId: string; locale: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/methodology", { params: { path: { case_id: caseId } } })), [caseId], { cache: "report/Report#Methodology:found" });
  const count = (n: number) => new Intl.NumberFormat(locale).format(n);
  return (
    <section className="rp-card" aria-labelledby="method-title">
      <h2 id="method-title" className="rp-card__title">{w("report.methodology")}</h2>
      <p className="av-muted rp-small">{w("report.methodology_body")}</p>
      <details className="rp-wording">
        <summary className="av-text-link">{w("report.wording")}</summary>
        {found.data && <p className="av-muted rp-small">{w("report.pages_read", { read: count(found.data.pages_read), total: count(found.data.pages_total) })}</p>}
      </details>
    </section>
  );
}

function Preview({ caseId, pages }: { caseId: string; pages: number }) {
  const [page, setPage] = useState(1);
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null;
    let live = true;
    void unwrap(api.GET("/v1/cases/{case_id}/report/preview/{page}", { params: { path: { case_id: caseId, page } }, parseAs: "blob" }))
      .then((blob) => {
        if (!live) return;
        made = URL.createObjectURL(blob as Blob);
        setSrc(made);
      })
      .catch(() => undefined);
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [caseId, page]);
  return (
    <details className="rp-wording rp-preview">
      <summary className="av-text-link">{w("report.preview_open")}</summary>
      <div className="av-stack">
        <img className="av-report-preview" src={src ?? undefined} alt={w("report.preview_alt", { page })} />
        <div className="av-row">
          <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>{w("report.preview_previous")}</Button>
          <span className="av-muted">{w("report.preview", { page, pages })}</span>
          <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>{w("report.preview_next")}</Button>
        </div>
      </div>
    </details>
  );
}
