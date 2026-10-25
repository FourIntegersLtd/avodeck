// The support console's case page (support plan phase 2; permissions.md "Avodeck staff"): where one case is
// stuck, as plain sections in counts, codes and dates. Staff only: the server answers anyone else with 404,
// which shows the not found page. No names and nothing from the pages ever reach this screen (rule 6).
import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { MessageNotice, NotFoundPage, OWN_ERRORS, type Message } from "./common";
import { w } from "./words";

type Detail = Schemas["SupportCaseDetail"];

function when(iso: string, locale: string | null | undefined, time = true): string {
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, time ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`support-${id}`} className="av-card av-org-card">
      <h2 id={`support-${id}`}>{title}</h2>
      {children}
    </section>
  );
}

export function SupportCase() {
  const { caseId = "" } = useParams();
  const { me } = useSession();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [hidden, setHidden] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const found = await unwrap(api.GET("/v1/support/cases/{case_id}", { params: { path: { case_id: caseId } } }));
        if (live) setDetail(found);
      } catch (err) {
        if (!live) return;
        if (err instanceof ApiError && err.status === 404) return setHidden(true);
        setMessage({ tone: "error", text: errorMessage(err, { ...OWN_ERRORS, server_error: "organisations.console.case.error" }) });
      }
    })();
    return () => {
      live = false;
    };
  }, [caseId]);

  if (hidden) return <NotFoundPage />;
  if (!detail && !message) return <p className="av-page av-muted" role="status">{w("console.case.loading")}</p>;
  const locale = me.person.locale;
  return (
    <div className="av-page av-page--narrow av-stack">
      <Link to="/support">{w("console.case.back")}</Link>
      <h1>{w("console.case.title")}</h1>
      <p>{w("console.case.intro")}</p>
      <p className="av-field__label">{caseId}</p>
      <MessageNotice message={message} />
      {detail && <Body detail={detail} locale={locale} />}
    </div>
  );
}

function Body({ detail: d, locale }: { detail: Detail; locale: string | null | undefined }) {
  const r = d.report;
  const c = d.close;
  return (
    <>
      <Section id="jobs" title={w("console.case.jobs.title")}>
        {d.jobs.length === 0 && <p className="av-muted">{w("console.case.none")}</p>}
        {d.jobs.length > 0 && (
          <ul className="av-stack" role="list">
            {d.jobs.map((j) => (
              <li key={j.id}>
                <p>
                  <strong>{w(`console.case.job_status.${j.status}`)}</strong>
                </p>
                <p className="av-muted">{w("console.case.jobs.line", { kind: j.kind ?? "", attempts: j.attempts, when: when(j.updated_at, locale) })}</p>
                {j.error_code && <p className="av-muted">{w("console.case.jobs.code", { code: j.error_code })}</p>}
                {j.status === "paused" && <p>{w("console.case.jobs.paused_explain")}</p>}
                {j.status === "failed" && <p>{w("console.case.jobs.failed_explain", { attempts: j.attempts })}</p>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section id="pages" title={w("console.case.pages.title")}>
        <p>{w("console.case.pages.total", { count: d.pages.total })}</p>
        {d.pages.total > 0 && <p>{d.pages.unread_by_named_expert > 0 ? w("console.case.pages.unread", { count: d.pages.unread_by_named_expert }) : w("console.case.pages.all_read")}</p>}
        {d.pages.unreadable > 0 && <p>{w("console.case.pages.unreadable", { count: d.pages.unreadable })}</p>}
        {d.pages.needs_clearer_copy > 0 && <p>{w("console.case.pages.needs_clearer_copy", { count: d.pages.needs_clearer_copy })}</p>}
        {d.pages.quarantined > 0 && <p>{w("console.case.pages.quarantined", { count: d.pages.quarantined })}</p>}
        {d.pages.superseded > 0 && <p>{w("console.case.pages.superseded", { count: d.pages.superseded })}</p>}
      </Section>

      <Section id="facts" title={w("console.case.facts.title")}>
        <p>{w("console.case.facts.suggested", { count: d.facts.suggested })}</p>
        <p>{w("console.case.facts.verified", { count: d.facts.verified })}</p>
        {d.facts.needs_recheck > 0 && <p>{w("console.case.facts.recheck", { count: d.facts.needs_recheck })}</p>}
      </Section>

      <Section id="questions" title={w("console.case.questions.title")}>
        <p>{w(`console.case.questions.state.${d.questions.state}`)}</p>
        {d.questions.instruction_versions.map((v) => (
          <div key={v.n}>
            <p>{w("console.case.questions.letter", { n: v.n, status: w(`console.case.questions.read.${v.read_status}`) })}</p>
            {v.job_error_code && <p className="av-muted">{w("console.case.jobs.code", { code: v.job_error_code })}</p>}
            {v.read_status === "failed" && <p>{w("console.case.questions.failed_explain")}</p>}
          </div>
        ))}
      </Section>

      <Section id="report" title={w("console.case.report.title")}>
        {r.latest_version_status ? (
          <p>{w("console.case.report.status", { status: w(`console.case.report.status.${r.latest_version_status}`) })}</p>
        ) : (
          <p className="av-muted">{w("console.case.report.none")}</p>
        )}
        {r.locked_at && <p>{w("console.case.report.locked_at", { when: when(r.locked_at, locale) })}</p>}
        {r.served_at && <p>{w("console.case.report.served_at", { when: when(r.served_at, locale) })}</p>}
        {r.locked_at && <p>{w("console.case.report.locked_explain")}</p>}
        <p>{w(`console.case.report.check.${r.final_check.status}`)}</p>
        {r.final_check.item_codes.length > 0 && <p className="av-muted">{w("console.case.report.check.codes", { codes: r.final_check.item_codes.join(", ") })}</p>}
        <p>{r.declaration_acknowledged ? w("console.case.report.declaration_yes") : w("console.case.report.declaration_no")}</p>
        {r.pack_correction_pending && <p>{w("console.case.report.correction")}</p>}
        <p>{!r.signature_present ? w("console.case.report.signature_no") : r.signature_readable ? w("console.case.report.signature_yes") : w("console.case.report.signature_unreadable")}</p>
      </Section>

      <Section id="close" title={w("console.case.close.title")}>
        <p>{w("console.case.close.status", { status: c.status })}</p>
        <p>{c.delete_after ? w("console.case.close.delete_after", { when: when(c.delete_after, locale, false) }) : w("console.case.close.no_delete_after")}</p>
        {c.hold.present && (
          <>
            <p>{c.hold.until ? w("console.case.close.hold", { when: when(c.hold.until, locale, false), code: c.hold.reason_code ?? "" }) : w("console.case.close.hold_open", { code: c.hold.reason_code ?? "" })}</p>
            <p>{w("console.case.close.hold_explain")}</p>
          </>
        )}
        {c.deletion_started && <p>{w("console.case.close.deletion_started")}</p>}
        <p>{c.export.status === "none" ? w("console.case.close.export.none") : w("console.case.close.export", { status: w(`console.case.job_status.${c.export.status}`) })}</p>
        {c.export.downloaded && <p>{w("console.case.close.export_downloaded")}</p>}
      </Section>

      <Section id="offline" title={w("console.case.offline.title")}>
        {d.offline_packages.length === 0 && <p className="av-muted">{w("console.case.none")}</p>}
        {d.offline_packages.map((o) => (
          <div key={o.id}>
            <p>{w("console.case.offline.line", { state: w(`console.case.offline.state.${o.state}`), when: o.expires_at ? when(o.expires_at, locale) : "" })}</p>
            {o.stale_reason && <p>{w("console.case.offline.stale", { code: o.stale_reason })}</p>}
            {o.downloaded_at && <p className="av-muted">{w("console.case.offline.downloaded_at", { when: when(o.downloaded_at, locale) })}</p>}
          </div>
        ))}
      </Section>

      <Section id="named" title={w("console.case.named.title")}>
        {d.named_expert_history.length === 0 && <p className="av-muted">{w("console.case.none")}</p>}
        {d.named_expert_history.map((h) => (
          <p key={h.changed_at}>{w("console.case.named.line", { when: when(h.changed_at, locale) })}</p>
        ))}
        {d.named_expert_history.length > 0 && <p>{w("console.case.named.explain")}</p>}
      </Section>

      <Section id="deadlines" title={w("console.case.deadlines.title")}>
        <p>{w("console.case.deadlines.counts", { ...d.deadlines })}</p>
      </Section>
    </>
  );
}
