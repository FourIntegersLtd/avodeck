// S16 Your report (docs/design/screens/S16-Report.dc.html): the sections (headings from the pack's labels), what the
// court requires (from the pack), the citation style, building and rebuilding, the expert's own sections and the
// methodology note. The server decides every change; edits send the version this screen saw.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBox, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { useTimeHeartbeat } from "./heartbeat";
import { useSectionName, useWaiting, versionTag } from "./shared";
import { w } from "./words";

type Report = Schemas["ReportOut"];
const STYLES = ["page", "paragraph"] as const;
const ERRORS = { stale: "report.stale", not_built: "report.not_built" };

export function ReportScreen() {
  const { caseId = "" } = useParams();
  useTimeHeartbeat(caseId, "report");
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report", { params: { path: { case_id: caseId } } })), [caseId]);
  const settings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/settings", { params: { path: { case_id: caseId } } })), [caseId]);
  const wait = useWaiting(found.reload, found.data?.version?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [styleVersion, setStyleVersion] = useState<number | null>(null);

  if (found.error || settings.error) return <div className="av-page"><LoadError onRetry={() => { found.reload(); settings.reload(); }} /></div>;
  if (found.data === undefined || !settings.data) return <div className="av-page"><Loading /></div>;
  const data = (found.data ?? { version: null, sections: [], checklist: { items: [] } }) as unknown as Report;
  const version = data.version;
  const currentStyle = style ?? settings.data.citation_style;
  const currentVersion = styleVersion ?? settings.data.version;

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

  const changeStyle = (next: (typeof STYLES)[number]) =>
    attempt(async () => {
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

  const done = data.checklist.items.filter((item) => item.done).length;

  return (
    <div className="av-page av-stack">
      <h2>{w("report.title")}</h2>
      <p>{w("report.intro")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {wait.waiting && <Notice><p>{w("building")}</p></Notice>}
      {!version && <Notice><p>{w("report.empty")}</p></Notice>}
      {version && (
        <p>
          <strong>{version.page_count ? w("report.version", { name: version.display_name, pages: version.page_count }) : w("report.version_nopages", { name: version.display_name })}</strong>
        </p>
      )}
      {version?.template_fallback && <Notice tone="warning"><p>{w("report.fallback")}</p></Notice>}

      <fieldset className="av-report-fieldset">
        <legend>{w("report.style")}</legend>
        <div className="av-row">
          {STYLES.map((key) => (
            <label key={key} className="av-report-radio">
              <input type="radio" name="citation_style" checked={currentStyle === key} onChange={() => void changeStyle(key)} />
              <span>{w(`report.style.${key}`)}</span>
            </label>
          ))}
        </div>
        <p className="av-muted">{w("report.style_changed")}</p>
      </fieldset>

      <div className="av-report-columns">
        <Sections caseId={caseId} data={data} onSaved={found.reload} />
        <div className="av-stack">
          <section className="av-card av-stack" aria-labelledby="checklist-title">
            <h3 id="checklist-title">{w("report.checklist")}</h3>
            <ul className="av-report-list">
              {data.checklist.items.map((item) => (
                <li key={item.key} className="av-report-item">
                  <span aria-hidden="true">{item.done ? "✓" : "○"}</span> <span>{item.text ?? humanise(item.key)}</span>
                </li>
              ))}
            </ul>
            <p className="av-muted">{w("report.checklist_done", { done, total: data.checklist.items.length })}</p>
          </section>
          <Methodology caseId={caseId} />
          {version && <Preview caseId={caseId} pages={version.page_count ?? 1} />}
        </div>
      </div>

      <div className="av-report-footer">
        <Button variant="secondary" onClick={() => void build()} disabled={wait.waiting}>
          {version ? w("report.rebuild") : w("report.build")}
        </Button>
        {version && <LinkButton to={`/cases/${caseId}/final-check`}>{w("report.final_check")}</LinkButton>}
      </div>
    </div>
  );
}

function Sections({ caseId, data, onSaved }: { caseId: string; data: Report; onSaved: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const nameOf = useSectionName();
  return (
    <section className="av-card av-stack" aria-labelledby="sections-title">
      <h3 id="sections-title">{w("report.sections")}</h3>
      <ul className="av-report-list">
        {data.sections.map((section) => {
          const name = nameOf(section.key);
          return (
            <li key={section.key} aria-label={name} className="av-report-item av-report-item--spread">
              <span>
                <strong>{name}</strong>{" "}
                <span className="av-badge">{section.written ? w("report.section.written") : section.generated ? w("report.section.generated") : w("report.section.todo")}</span>
              </span>
              <Button variant="quiet" aria-label={`${section.written ? w("report.section.edit") : w("report.section.write")} ${name}`} onClick={() => setOpen(open === section.key ? null : section.key)}>
                {section.written ? w("report.section.edit") : w("report.section.write")}
              </Button>
            </li>
          );
        })}
      </ul>
      {open && <SectionEditor key={open} caseId={caseId} sectionKey={open} onClose={() => setOpen(null)} onSaved={onSaved} />}
    </section>
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

function Methodology({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/methodology", { params: { path: { case_id: caseId } } })), [caseId]);
  return (
    <section className="av-card av-stack" aria-labelledby="method-title">
      <h3 id="method-title">{w("report.methodology")}</h3>
      <p>{w("report.methodology_body")}</p>
      {found.data && <p className="av-muted">{w("report.pages_read", { read: found.data.pages_read, total: found.data.pages_total })}</p>}
    </section>
  );
}

function Preview({ caseId, pages }: { caseId: string; pages: number }) {
  const [page, setPage] = useState(1);
  return (
    <section className="av-card av-stack">
      <img className="av-report-preview" src={`${apiOrigin()}/v1/cases/${caseId}/report/preview/${page}`} alt={w("report.preview_alt", { page })} />
      <div className="av-row">
        <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>{w("report.preview_previous")}</Button>
        <span className="av-muted">{w("report.preview", { page })}</span>
        <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>{w("report.preview_next")}</Button>
      </div>
    </section>
  );
}
