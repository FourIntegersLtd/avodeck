// Screen 08, Your bundle is ready: the API's bundle summary, with the pack's source words and colours,
// a card for each thing the expert should know, the files table and one main action.
import type { ReactNode } from "react";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { IconTile, LinkButton, LoadError, Loading, Notice, TextLink } from "../../foundation/ui";
import { can, casePath, documentsOf, requestLabel, sourceWord } from "./data";
import { formatDate, formatNumber, languageName, pageList } from "./format";
import { AlertIcon, CopyIcon, MicIcon, OtherPersonIcon, PageIcon, SearchIcon } from "./icons";
import { SourceStack } from "./SourceStack";
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
  const kind = exhibits.length > 0 && exhibits.every((e) => e.media_type.startsWith("audio/")) ? "audio" : "other";
  const keptParts = [exhibits.length > 0 && wn(`bundle.kept_${kind}`, exhibits.length, locale), foreign.length > 0 && wn("bundle.not_english", foreign.length, locale)].filter(Boolean) as string[];
  const keptTitle = keptParts.length > 1 ? w("bundle.and", { first: keptParts[0], second: keptParts[1] }) : w("bundle.sentence", { text: keptParts[0] });

  return (
    <div className="av-page">
      <h1>{w("bundle.title")}</h1>
      <div className="pl-ready">
        <section className="av-card pl-sources">
          <SourceBar pack={pack} bySource={s.by_source} />
        </section>
        <div className="pl-cards">
          <Card icon={<PageIcon />} title={w("bundle.numbered", { pages: num(s.pages), last: num(last) })}>
            <p>{w("bundle.numbered_note", { last: num(last) })}</p>
          </Card>
          {s.duplicates > 0 && (
            <Card icon={<CopyIcon />} title={wn("bundle.copies", s.duplicates, locale)} action={<TextLink to={`${base}/copies`}>{w("bundle.see_copies")}</TextLink>}>
              <p>{w("bundle.copies_note")}</p>
            </Card>
          )}
          {suggested.length > 0 && (
            <Card icon={<OtherPersonIcon />} tone="amber" title={wn("bundle.other_person", suggested.length, locale)}>
              <p>{w("bundle.other_person_note", { pages: pageList(suggested.map((q) => q.page_no), locale) })}</p>
              {can(item, "page.quarantine") && (
                <LinkButton to={`${base}/not-this-patient`} variant="secondary">{w("bundle.check")}</LinkButton>
              )}
            </Card>
          )}
          {setAside.length > 0 && <Card icon={<OtherPersonIcon />} title={wn("bundle.set_aside", setAside.length, locale)} />}
          {(exhibits.length > 0 || foreign.length > 0) && (
            <Card icon={<MicIcon />} tone="grey" title={keptTitle}>
              <p>
                {exhibits.length > 0 && wn(`bundle.kept_note_${kind}`, exhibits.length, locale)}
                {exhibits.length > 0 && foreign.length > 0 && " "}
                {foreign.length > 0 &&
                  wn("bundle.translate", foreign.length, locale, {
                    pages: foreign.map((f) => w("bundle.foreign_page", { page: num(f.page_no), language: languageName(f.language, locale) })).join("; "),
                  })}
              </p>
            </Card>
          )}
          {s.unreadable > 0 && (
            <Card icon={<SearchIcon />} tone="amber" title={wn("bundle.unreadable", s.unreadable, locale)}>
              <p>{w("bundle.unreadable_note")}</p>
            </Card>
          )}
          {(missing.length > 0 || s.gaps > 0) && (
            <Card
              icon={<AlertIcon />}
              tone="amber"
              centred
              title={missing.length > 0 ? wn("bundle.missing", missing.length, locale) : wn("bundle.gaps", s.gaps, locale)}
              action={label && can(item, "request.draft") ? <LinkButton to={`${base}/ask`} variant="secondary">{label}</LinkButton> : undefined}
              below={
                missing.length > 0 ? (
                  <>
                    <ul className="pl-missing">
                      {missing.map((m) => (
                        <li key={m.id}>{m.description}</li>
                      ))}
                    </ul>
                    {s.gaps > 0 && <p>{wn("bundle.gaps", s.gaps, locale)}</p>}
                  </>
                ) : undefined
              }
            />
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

      <section className="av-card pl-section pl-files">
        <h2 className="av-visually-hidden">{w("bundle.files")}</h2>
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
                <th scope="row" className="pl-strong">{d.filename}</th>
                <td className="av-mono">{d.pages != null ? num(d.pages) : w("bundle.not_given")}</td>
                <td>{d.obtained_how ?? w("bundle.not_given")}</td>
                <td>{d.received_on ? formatDate(d.received_on, locale) : w("bundle.not_given")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="pl-actions">
        <LinkButton to={reader} arrow>{w("bundle.start")}</LinkButton>
      </div>
    </div>
  );
}

interface CardProps {
  icon: ReactNode;
  title: string;
  tone?: "amber" | "grey";
  /** Centre the title against the action (a card whose list sits below). */
  centred?: boolean;
  /** A link or button at the right of the card. */
  action?: ReactNode;
  children?: ReactNode;
  /** Goes under the title row, lined up with the text. */
  below?: ReactNode;
}

function Card({ icon, title, tone, centred, action, children, below }: CardProps) {
  return (
    <section className={["av-card", "pl-card", tone === "amber" && "pl-card--amber", centred && "pl-card--centred"].filter(Boolean).join(" ")}>
      <div className="pl-card__row">
        <IconTile tone={tone ?? "mint"}>{icon}</IconTile>
        <div className="pl-card__text">
          <h2 className="pl-card__title">{title}</h2>
          {children}
        </div>
        {action}
      </div>
      {below && <div className="pl-card__below">{below}</div>}
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
  const colour = (key: string) => types.find((t) => t.key === key)?.colour ?? UNKNOWN_COLOUR;
  const label = (key: string) => (key ? (sourceWord(pack, key) ?? humanise(key)) : w("bundle.source_unknown"));
  const locale = pack.locale;
  return (
    <SourceStack
      label={w("bundle.sources")}
      rows={rows.map((r) => ({ key: r.key, pages: r.pages, colour: colour(r.key), label: label(r.key) }))}
      format={(n) => formatNumber(n, locale)}
      unit={(n) => wn("bundle.tile_unit", n, locale)}
    />
  );
}
