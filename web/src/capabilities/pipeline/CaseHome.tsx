// Case home (board 03b): the papers the expert was sent, grouped by kind, and where the case stands.
// Reads only existing endpoints: bundle summary, progress, per-step progress and the questions list.
import { useState } from "react";
import { Link } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { firstScreenInCase, screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, ProgressBar, SectionIcon, StatusMark, stepLabel } from "../../foundation/ui";
import { can, casePath, documentsOf, kindWord } from "./data";
import { formatDate, formatNumber } from "./format";
import { w, wn } from "./words";

/** The core upload kinds, in the order the board shows them; any other kind follows. */
const KIND_ORDER = ["letter", "records", "other"];

type Doc = Schemas["Document"];

function groupsOf(docs: Doc[]): Array<{ kind: string; files: Doc[] }> {
  const by = new Map<string, Doc[]>();
  for (const d of docs) by.set(d.kind, [...(by.get(d.kind) ?? []), d]);
  const rank = (k: string) => (KIND_ORDER.includes(k) ? KIND_ORDER.indexOf(k) : KIND_ORDER.length);
  return [...by.entries()].sort((a, b) => rank(a[0]) - rank(b[0])).map(([kind, files]) => ({ kind, files }));
}

export function CaseHome() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const locale = pack.locale;
  const summary = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", casePath(caseId))), [caseId]);
  const progress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/progress", casePath(caseId))), [caseId]);
  const stepProgress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/steps/progress", casePath(caseId))), [caseId]);
  const questions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId]);
  const [shut, setShut] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState<Set<string>>(new Set());
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const num = (n: number) => formatNumber(n, locale);

  const removeFile = async (documentId: string) => {
    if (!confirm(w("home.delete_confirm"))) return;
    setDeleting((prev) => new Set(prev).add(documentId));
    setDeleteError(null);
    try {
      await unwrap(
        api.DELETE("/v1/cases/{case_id}/documents/{document_id}", {
          params: { path: { case_id: caseId, document_id: documentId } },
        }),
      );
      summary.reload();
    } catch (err) {
      setDeleteError(errorMessage(err));
    } finally {
      setDeleting((prev) => { const next = new Set(prev); next.delete(documentId); return next; });
    }
  };

  if (summary.error) return <div className="av-page"><LoadError onRetry={summary.reload} /></div>;
  if (!summary.data) return <div className="av-page"><Loading /></div>;
  const s = summary.data;
  const groups = groupsOf(documentsOf(s));
  const toggle = (kind: string) => setShut((prev) => { const next = new Set(prev); if (!next.delete(kind)) next.add(kind); return next; });
  const reader = (page: number) => screenPath("reader", { caseId, params: { page } });
  const addPapers = can(item, "case.upload") ? screenPath("add_papers", { caseId }) : null;

  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  const stage = (item as { stage?: string | null }).stage;
  const currentIndex = steps.findIndex((x) => x.key === stage);
  const notes = Object.fromEntries((stepProgress.data?.steps ?? []).map((p) => [p.key, p]));
  const note = (key: string) => {
    const p = notes[key];
    if (!p || p.percent === null || p.done === null || p.total === null) return "";
    return p.total >= 20 ? t("ui.case_header.progress", { percent: p.percent }) : t("ui.case_header.progress_count", { done: p.done, total: p.total });
  };

  const read = progress.data?.read ?? 0;
  const total = progress.data?.total ?? 0;
  const current = steps[currentIndex];
  const reading = !!current?.screens?.includes("reader");
  const nextPage = Math.min(read + 1, Math.max(total, 1));
  const resume = reading ? reader(nextPage) : currentIndex >= 0 ? firstScreenInCase(current?.screens, caseId) : null;
  const questionsPath = screenPath("questions", { caseId });

  return (
    <div className="av-page pl-home">
      <div className="pl-home__main">
        <div className="pl-home__head">
          <div>
            <h1>{w("home.title")}</h1>
            <p>{w("home.lead")}</p>
          </div>
          {addPapers && (groups.length === 0
            ? <LinkButton to={addPapers}>{w("home.add_first")}</LinkButton>
            : <LinkButton to={addPapers} variant="secondary">{w("home.add")}</LinkButton>)}
        </div>

        {groups.map((g, i) => {
          const open = !shut.has(g.kind);
          const title = kindWord(pack, g.kind);
          const pages = g.files.reduce((n, f) => n + (f.pages ?? 0), 0);
          return (
            <section key={g.kind} className="av-card pl-group" aria-label={title}>
              <button type="button" className="pl-group__toggle" aria-expanded={open} onClick={() => toggle(g.kind)}>
                <span className={`pl-group__swatch pl-group__swatch--${Math.min(i, 2)}`} aria-hidden="true" />
                <span className="pl-group__name">
                  <strong>{title}</strong>
                  <span>{wn("home.group_summary", g.files.length, locale, { pages: wn("home.pages", pages, locale) })}</span>
                </span>
                <span className="pl-group__chev">{open ? w("home.hide") : w("home.show")}</span>
              </button>
              {open && (
                <ul className="pl-plain">
                  {g.files.map((f) => {
                    const to = f.first_page_no != null ? reader(f.first_page_no) : null;
                    return (
                      <li key={f.id} className="pl-file">
                        <span className="pl-file__name">
                          <strong>{f.filename}</strong>
                          <span>{f.obtained_how ?? w("bundle.not_given")}</span>
                        </span>
                        <span className="av-mono pl-file__meta">{f.pages != null ? wn("home.pages", f.pages, locale) : w("bundle.not_given")}</span>
                        <span className="pl-file__meta">{f.received_on ? w("home.received", { date: formatDate(f.received_on, locale) }) : w("bundle.not_given")}</span>
                        {to ? (
                          <Link className="pl-file__open" to={to} aria-label={w("home.open_file", { name: f.filename })}>{w("home.open")}</Link>
                        ) : (
                          <span className="pl-file__meta">{w("home.not_numbered")}</span>
                        )}
                        {can(item, "case.upload") && (
                          <Button
                            variant="danger"
                            className="pl-file__remove"
                            busy={deleting.has(f.id)}
                            onClick={() => removeFile(f.id)}
                            aria-label={w("home.delete_file", { name: f.filename })}
                          >{w("home.delete")}</Button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          );
        })}

        {deleteError && <p className="av-error" role="alert">{deleteError}</p>}
        <p className="pl-file__meta">{w("home.keep_numbers")}</p>
      </div>

      <aside className="pl-home__side">
        <section className="av-card pl-section" aria-label={w("home.where")}>
          <h2>{w("home.where")}</h2>
          <ol className="pl-home__steps" aria-label={w("home.steps")}>
            {steps.map((st, i) => (
              <li key={st.key} className="pl-home__step" aria-current={i === currentIndex ? "step" : undefined}>
                {currentIndex >= 0 && i < currentIndex ? <StatusMark status="checked" /> : <span className={`pl-home__dot${i === currentIndex ? " pl-home__dot--current" : ""}`} aria-hidden="true" />}
                <span>{stepLabel(st.key)}</span>
                <span className="av-mono pl-file__meta">{note(st.key)}</span>
              </li>
            ))}
          </ol>
          {total > 0 && (
            <>
              <ProgressBar value={read} max={total} label={w("home.read", { read: num(read), total: num(total) })} />
              <p>{w("home.read", { read: num(read), total: num(total) })}</p>
            </>
          )}
          {resume && <LinkButton to={resume} arrow>{reading ? w("home.carry_on_reading", { page: num(nextPage) }) : w("home.carry_on")}</LinkButton>}
        </section>

        <section className="av-card pl-section" aria-label={w("home.about")}>
          <SectionIcon glyph="folder" />
          <h2>{w("home.about")}</h2>
          <dl className="pl-home__facts">
            {item.due_date && <div><dt>{w("home.due")}</dt><dd className="av-mono">{formatDate(item.due_date, locale)}</dd></div>}
            {questions.data && <div><dt>{w("home.questions")}</dt><dd className="av-mono">{num(questions.data.questions.length)}</dd></div>}
            <div><dt>{w("home.copies")}</dt><dd className="av-mono">{wn("home.pages", s.duplicates, locale)}</dd></div>
            <div><dt>{w("home.unreadable")}</dt><dd className="av-mono">{num(s.unreadable)}</dd></div>
          </dl>
          {questionsPath && <Link className="pl-file__open" to={questionsPath}>{w("home.see_questions")}</Link>}
        </section>
      </aside>
    </div>
  );
}
