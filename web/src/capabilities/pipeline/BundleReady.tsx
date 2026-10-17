// Screen 08, Your bundle is ready: the API's bundle summary, with the pack's source words and colours,
// a card for each thing the expert should know, the files table and one main action.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { can, casePath, documentsOf, requestLabel, sourceWord } from "./data";
import { formatDate, formatNumber, languageName, pageList } from "./format";
import { w, wn } from "./words";

const UNKNOWN_COLOUR = "#8A948E";
const STILL_MISSING = new Set(["open", "requested"]);

export function BundleReady() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const found = useResource(async () => {
    const path = casePath(caseId);
    const [summary, missing] = await Promise.all([
      unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", path)),
      unwrap(api.GET("/v1/cases/{case_id}/missing-items", path)),
    ]);
    return { summary, missing: missing.missing_items.filter((m) => STILL_MISSING.has(m.status)) };
  }, [caseId]);

  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const { summary: s, missing } = found.data;
  const locale = pack.locale;
  const num = (n: number) => formatNumber(n, locale);
  const base = `/cases/${caseId}/bundle`;

  if (s.pages === 0) {
    return (
      <div className="av-page av-stack">
        <h1>{w("bundle.title")}</h1>
        <Notice><p>{w("bundle.empty")}</p></Notice>
        <LinkButton to={`/cases/${caseId}/preparing`} variant="secondary">{w("bundle.to_preparing")}</LinkButton>
      </div>
    );
  }

  const last = Math.max(s.pages, ...s.versions.map((v) => v.last_page_no ?? 0));
  const suggested = (s.quarantine_suggestions ?? []).filter((q) => q.status === "suggested");
  const setAside = (s.quarantined ?? []).filter((q) => q.status === "confirmed" || q.status === "purged");
  const exhibits = s.exhibits ?? [];
  const foreign = s.foreign_language ?? [];
  const label = requestLabel(pack);
  const reader = screenPath("reader", { caseId }) ?? `/cases/${caseId}`;

  return (
    <div className="av-page">
      <h1>{w("bundle.title")}</h1>
      <div className="pl-ready">
        <div className="av-card pl-sources">
          <SourceBar pack={pack} bySource={s.by_source} />
        </div>
        <div className="pl-cards">
          <Card title={w("bundle.numbered", { pages: num(s.pages), last: num(last) })}>
            <p>{w("bundle.numbered_note", { last: num(last) })}</p>
          </Card>
          {s.duplicates > 0 && (
            <Card title={wn("bundle.copies", s.duplicates, locale)} action={<Link to={`${base}/copies`}>{w("bundle.see_copies")}</Link>}>
              <p>{w("bundle.copies_note")}</p>
            </Card>
          )}
          {suggested.length > 0 && (
            <Card tone="amber" title={wn("bundle.other_person", suggested.length, locale)}>
              <p>{w("bundle.other_person_note", { pages: pageList(suggested.map((q) => q.page_no), locale) })}</p>
              {can(item, "page.quarantine") && (
                <LinkButton to={`${base}/not-this-patient`} variant="secondary">{w("bundle.check")}</LinkButton>
              )}
            </Card>
          )}
          {setAside.length > 0 && <Card title={wn("bundle.set_aside", setAside.length, locale)} />}
          {(exhibits.length > 0 || foreign.length > 0) && (
            <Card title={exhibits.length > 0 ? wn("bundle.exhibits", exhibits.length, locale) : wn("bundle.foreign", foreign.length, locale)}>
              {exhibits.length > 0 && <p>{w("bundle.exhibits_note")}</p>}
              {exhibits.length > 0 && foreign.length > 0 && <p className="pl-strong">{wn("bundle.foreign", foreign.length, locale)}</p>}
              {foreign.length > 0 && (
                <p>
                  {w("bundle.foreign_note", {
                    pages: foreign.map((f) => w("bundle.foreign_page", { page: num(f.page_no), language: languageName(f.language, locale) })).join("; "),
                  })}
                </p>
              )}
            </Card>
          )}
          {s.unreadable > 0 && (
            <Card tone="amber" title={wn("bundle.unreadable", s.unreadable, locale)}>
              <p>{w("bundle.unreadable_note")}</p>
            </Card>
          )}
          {(missing.length > 0 || s.gaps > 0) && (
            <Card
              tone="amber"
              title={missing.length > 0 ? wn("bundle.missing", missing.length, locale) : wn("bundle.gaps", s.gaps, locale)}
              action={label && can(item, "request.draft") ? <LinkButton to={`${base}/ask`} variant="secondary">{label}</LinkButton> : undefined}
            >
              {missing.length > 0 && (
                <ul className="pl-missing">
                  {missing.map((m) => (
                    <li key={m.id}>{m.description}</li>
                  ))}
                </ul>
              )}
              {missing.length > 0 && s.gaps > 0 && <p>{wn("bundle.gaps", s.gaps, locale)}</p>}
            </Card>
          )}
        </div>
      </div>

      {s.versions.length > 1 && (
        <section className="av-card pl-section">
          <h2>{w("bundle.deliveries")}</h2>
          <ul className="pl-plain">
            {s.versions.map((v) => (
              <li key={v.bundle_version}>
                {w("bundle.delivery", { n: v.bundle_version, first: num(v.first_page_no ?? 0), last: num(v.last_page_no ?? 0) })}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="av-card pl-section">
        <h2>{w("bundle.files")}</h2>
        <table className="pl-table">
          <thead>
            <tr>
              <th scope="col">{w("bundle.file")}</th>
              <th scope="col">{w("bundle.pages")}</th>
              <th scope="col">{w("bundle.how")}</th>
              <th scope="col">{w("bundle.received")}</th>
            </tr>
          </thead>
          <tbody>
            {documentsOf(s).map((d) => (
              <tr key={d.id}>
                <th scope="row">{d.filename}</th>
                <td className="av-mono">{d.pages != null ? num(d.pages) : w("bundle.not_given")}</td>
                <td>{d.obtained_how ?? w("bundle.not_given")}</td>
                <td>{d.received_on ? formatDate(d.received_on, locale) : w("bundle.not_given")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="pl-actions">
        <LinkButton to={reader}>{w("bundle.start")}</LinkButton>
      </div>
    </div>
  );
}

function Card({ title, tone, action, children }: { title: string; tone?: "amber"; action?: ReactNode; children?: ReactNode }) {
  return (
    <section className={["av-card", "pl-card", tone && `pl-card--${tone}`].filter(Boolean).join(" ")}>
      <div className="pl-card__head">
        <h2 className="pl-card__title">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function SourceBar({ pack, bySource }: { pack: Schemas["CasePack"]; bySource: Schemas["BundleSummary"]["by_source"] }) {
  const types = pack.source_types as Array<{ key: string; colour?: string }>;
  const order = (key: string) => {
    const i = types.findIndex((t) => t.key === key);
    return i < 0 ? types.length : i;
  };
  const rows = bySource.filter((r) => r.pages > 0).map((r) => ({ key: r.source_type ?? "", pages: r.pages })).sort((a, b) => order(a.key) - order(b.key));
  const total = rows.reduce((n, r) => n + r.pages, 0) || 1;
  const colour = (key: string) => types.find((t) => t.key === key)?.colour ?? UNKNOWN_COLOUR;
  let y = 0;
  return (
    <div className="pl-sourcebar">
      <svg className="pl-sourcebar__bar" viewBox="0 0 100 400" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        {rows.map((r) => {
          const h = (r.pages / total) * 400;
          const rect = <rect key={r.key} x="0" y={y} width="100" height={h} fill={colour(r.key)} />;
          y += h;
          return rect;
        })}
      </svg>
      <ul className="pl-sourcebar__list" aria-label={w("bundle.sources")}>
        {rows.map((r) => (
          <li key={r.key}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <rect width="16" height="16" rx="3" fill={colour(r.key)} />
            </svg>
            <span className="pl-strong">{r.key ? (sourceWord(pack, r.key) ?? humanise(r.key)) : w("bundle.source_unknown")}</span>{" "}
            <span className="av-mono">{wn("bundle.source_pages", r.pages, pack.locale)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
