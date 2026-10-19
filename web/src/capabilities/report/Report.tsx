// S16 Your report (docs/design/screens/S16-Report.dc.html): the numbered sections at the left (headings from the
// pack's labels), the chosen section as a paper page in the middle, what the court requires and the methodology
// note at the right. The server decides every change; edits send the version this screen saw.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBox, LinkButton, LoadError, Loading, Notice, Segmented } from "../../foundation/ui";
import { downloadPath, useLocale, useSectionName, useWaiting, versionTag } from "./shared";
import { w } from "./words";

type Report = Schemas["ReportOut"];
const STYLES = ["page", "paragraph"] as const;
const ERRORS = { stale: "report.stale", not_built: "report.not_built" };

export function ReportScreen() {
  const { caseId = "" } = useParams();
  const { item, pack } = useCaseFrame();
  const locale = useLocale();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId]);
  const settings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/settings", { params: { path: { case_id: caseId } } })), [caseId]);
  const wait = useWaiting(found.reload, found.data?.version?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [styleVersion, setStyleVersion] = useState<number | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);

  if (found.error || settings.error) return <div className="av-page"><LoadError onRetry={() => { found.reload(); settings.reload(); }} /></div>;
  if (found.data === undefined || !settings.data) return <div className="av-page"><Loading /></div>;
  const data = (found.data ?? { version: null, sections: [], checklist: { items: [] } }) as unknown as Report;
  const version = data.version;
  const currentStyle = style ?? settings.data.citation_style;
  const currentVersion = styleVersion ?? settings.data.version;
  const selected = data.sections.find((s) => s.key === chosen) ?? data.sections[0] ?? null;

  const attempt = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
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

  const done = data.checklist.items.filter((entry) => entry.done).length;
  const appendices = ((pack.report_appendices ?? []) as Array<{ key: string; label?: string }>).map((a) => a.label ?? humanise(a.key));
  const limits = ((pack.compliance_checklist as unknown as { page_limit?: Record<string, number> } | undefined)?.page_limit ?? {}) as Record<string, number>;
  const limit = item.court_track ? limits[item.court_track] : undefined;
  const footer = [
    version ? (version.page_count ? w("report.version", { name: version.display_name, pages: version.page_count }) : w("report.version_nopages", { name: version.display_name })) : null,
    version && item.court_track ? (limit ? w("report.limit", { count: limit }) : w("report.no_limit")) : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="av-page rp-page rp-page--wide">
      <div className="rp-head">
        <div>
          <h1 className="rp-title">{w("report.title")}</h1>
          <p className="rp-lead">{w("report.intro")}</p>
        </div>
        <div className="rp-head__tools">
          <Segmented label={w("report.style")} hideLabel name="citation_style" value={currentStyle} onChange={(next) => void changeStyle(next)} options={STYLES.map((key) => ({ value: key, label: w(`report.style.${key}`) }))} />
          <span className="av-visually-hidden">{w("report.style_changed")}</span>
          {version && <a className="av-button av-button--secondary" href={`${apiOrigin()}${downloadPath(caseId, version.id, "docx")}`}>{w("report.open_word")}</a>}
        </div>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {wait.waiting && <Notice><p>{w("building")}</p></Notice>}
      {!version && <Notice><p>{w("report.empty")}</p></Notice>}
      {version?.template_fallback && <Notice tone="warning"><p>{w("report.fallback")}</p></Notice>}

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
            <p className="av-muted rp-small">{w("report.checklist_done", { done, total: data.checklist.items.length })}</p>
          </section>
          <Methodology caseId={caseId} locale={locale} />
        </aside>
      </div>

      <div className="rp-bar">
        <span className="av-muted rp-bar__note">{footer}</span>
        <div className="rp-bar__actions">
          {version ? (
            <button type="button" className="av-text-link rp-linkbtn" onClick={() => void build()} disabled={wait.waiting}>{w("report.rebuild")}</button>
          ) : (
            <Button onClick={() => void build()} disabled={wait.waiting}>{w("report.build")}</Button>
          )}
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
            <li key={section.key} aria-label={name}>
              <button type="button" className={`rp-contents__row${section.key === selected ? " rp-contents__row--on" : ""}`} aria-current={section.key === selected ? "true" : undefined} aria-label={name} onClick={() => onChoose(section.key)}>
                <span aria-hidden="true"><span>{index + 1}</span> <span>{name}</span></span>
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
              </button>
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
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/paragraphs", { params: { path: { case_id: caseId }, query: { section: section.key } } })), [caseId, section.key, data.version?.id]);
  return (
    <div className="rp-centre">
      <section className="rp-paper" aria-label={w("report.paper")} aria-live="polite">
        <h2 className="rp-paper__heading">{`${number}. ${name}`}</h2>
        {found.error ? <LoadError onRetry={found.reload} /> : !found.data ? <Loading /> : found.data.paragraphs.length === 0 ? (
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
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/sections/{key}", { params: { path: { case_id: caseId, key: sectionKey } } })), [caseId, sectionKey]);
  if (found.error) return <LoadError onRetry={found.reload} />;
  if (!found.data) return <Loading />;
  return <SectionForm caseId={caseId} initial={found.data} onClose={onClose} onSaved={onSaved} />;
}

function SectionForm({ caseId, initial, onClose, onSaved }: { caseId: string; initial: Schemas["SectionText"]; onClose: () => void; onSaved: () => void }) {
  const [body, setBody] = useState(initial.body);
  const [version, setVersion] = useState(initial.version);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const nameOf = useSectionName();
  const save = async () => {
    setError(null);
    setSaved(false);
    try {
      const next = await unwrap(
        api.PUT("/v1/cases/{case_id}/report/sections/{key}", {
          params: { path: { case_id: caseId, key: initial.key } },
          body: { body, citations: initial.citations },
          headers: ifMatch(versionTag(version)),
        }),
      );
      setVersion(next.version);
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <div className="av-stack">
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <DictationBox caseId={caseId} context="report_section" label={w("report.section.label", { name: nameOf(initial.key) })} value={body} onChange={(value) => { setBody(value); setSaved(false); }} rows={10} />
      <div className="av-row">
        <Button onClick={() => void save()}>{w("report.section.save")}</Button>
        <Button variant="secondary" onClick={onClose}>{w("report.section.close")}</Button>
        {saved && <span role="status">{w("report.section.saved")}</span>}
      </div>
    </div>
  );
}

function Methodology({ caseId, locale }: { caseId: string; locale: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/methodology", { params: { path: { case_id: caseId } } })), [caseId]);
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
  return (
    <details className="rp-wording rp-preview">
      <summary className="av-text-link">{w("report.preview_open")}</summary>
      <div className="av-stack">
        <img className="av-report-preview" src={`${apiOrigin()}/v1/cases/${caseId}/report/preview/${page}`} alt={w("report.preview_alt", { page })} />
        <div className="av-row">
          <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>{w("report.preview_previous")}</Button>
          <span className="av-muted">{w("report.preview", { page, pages })}</span>
          <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>{w("report.preview_next")}</Button>
        </div>
      </div>
    </details>
  );
}
