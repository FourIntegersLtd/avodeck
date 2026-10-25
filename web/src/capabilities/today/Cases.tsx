// S03c Cases (every case in one searchable list) and S03e Cases (empty).
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDue, t } from "../../foundation/i18n";
import { partyLine } from "../../foundation/lib/pack";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { IconTile, LinkButton, LoadError, Loading, SegmentedControl, stepLabel } from "../../foundation/ui";
import { BalanceFooter, Icon, StepRing, caseHref, combination, usePacks, type CaseCard, type CasePack } from "./shared";

export function Cases() {
  const { me } = useSession();
  // The server searches and filters; the address holds what was asked, so a reload or a link keeps it.
  const [params, setParams] = useSearchParams();
  const org = params.get("organisation_id");
  const q = params.get("q") ?? "";
  const [query, setQuery] = useState(q);
  useEffect(() => {
    if (query.trim() === q) return;
    const wait = setTimeout(() => setParams((p) => withParam(p, "q", query.trim()), { replace: true }), 250);
    return () => clearTimeout(wait);
  }, [query, q, setParams]);
  const setOrg = (next: string | null) => setParams((p) => withParam(p, "organisation_id", next ?? ""), { replace: true });
  const today = useResource(
    () => unwrap(api.GET("/v1/today", { params: { query: { ...(org ? { organisation_id: org } : {}), ...(q ? { q } : {}) } } })),
    [org, q],
    { cache: "today/Cases#Cases:today" },
  );
  const packs = usePacks(today.data?.cases);
  const data = today.data;
  // Empty means the person has no open case at all (the chips' counts ignore the search), not "nothing matched".
  const empty = Boolean(data) && data!.organisations.every((o) => o.open_count === 0) && data!.cases.length === 0;
  return (
    <div className="av-page">
      <div className="av-today__head">
        <div>
          <h1 className="av-today__title">{t("cases.title")}</h1>
          <p className="av-muted">{t(empty ? "cases.empty.intro" : "cases.intro")}</p>
        </div>
        {hasScreen("new_case") && !empty && (
          <LinkButton to={screenPath("new_case")!}>
            <Icon d="M12 5v14M5 12h14" />
            {t("today.cases.new")}
          </LinkButton>
        )}
      </div>
      {today.error && <LoadError onRetry={today.reload} />}
      {!data && !today.error && <Loading />}
      {data && empty && <CasesEmpty />}
      {data && !empty && (
        <>
          <List data={data} packs={packs.data} locale={me.person.locale} org={org} setOrg={setOrg} query={query} setQuery={setQuery} />
          <BalanceFooter packs={packs.data} />
        </>
      )}
    </div>
  );
}

function withParam(p: URLSearchParams, key: string, value: string) {
  const next = new URLSearchParams(p);
  if (value) next.set(key, value);
  else next.delete(key);
  return next;
}

interface ListProps {
  data: Schemas["Today"];
  packs: Map<string, CasePack> | undefined;
  locale: string | null | undefined;
  org: string | null;
  setOrg: (org: string | null) => void;
  query: string;
  setQuery: (q: string) => void;
}

function List({ data, packs, locale, org, setOrg, query, setQuery }: ListProps) {
  const shown = data.cases;
  const orgName = (o: Schemas["TodayOrganisation"]) => (o.kind === "personal" ? t("today.filter.personal") : o.name);
  return (
    <section aria-labelledby="your-cases" className="av-section">
      <h2 id="your-cases" className="av-visually-hidden">
        {t("today.cases.title")}
      </h2>
      <div className="av-cases-tools">
        <input type="search" className="av-input av-cases-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("cases.search")} aria-label={t("cases.search")} />
        {data.organisations.length > 1 && (
          <SegmentedControl
            label={t("today.filter.label")}
            value={org ?? "all"}
            onChange={(v) => setOrg(v === "all" ? null : v)}
            options={[
              { value: "all", label: t("today.filter.option", { name: t("today.filter.all"), count: data.organisations.reduce((n, o) => n + o.open_count, 0) }) },
              ...data.organisations.map((o) => ({ value: o.id, label: t("today.filter.option", { name: orgName(o), count: o.open_count }) })),
            ]}
          />
        )}
        <p className="av-muted">{t("today.cases.counts", { open: data.open_count, closed: data.closed_count })}</p>
      </div>
      {shown.length === 0 ? (
        <p>{t("cases.none_match")}</p>
      ) : (
        <div className="av-card av-cases-table">
          <div className="av-cases-row av-cases-row--head" aria-hidden="true">
            <span>{t("cases.col.case")}</span>
            <span>{t("cases.col.side")}</span>
            <span>{t("cases.col.step")}</span>
            <span>{t("cases.col.due")}</span>
          </div>
          <ul className="av-cases-list">
            {shown.map((c) => (
              <li key={c.case_id}>
                <Row card={c} pack={packs?.get(combination(c))} locale={locale} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Row({ card, pack, locale }: { card: CaseCard; pack: CasePack | undefined; locale: string | null | undefined }) {
  const steps = (pack?.workflow_steps ?? []) as Array<{ key: string }>;
  const index = steps.findIndex((s) => s.key === card.stage);
  return (
    <Link to={caseHref(card.case_id)} className="av-cases-row">
      <span className="av-cases-row__name">
        <strong>{card.label}</strong>
        {card.organisation_kind !== "personal" && <span className="av-badge av-badge--org">{card.organisation_name}</span>}
        {card.on_hold && <span className="av-badge av-badge--look">{t("case.on_hold")}</span>}
      </span>
      <span>{partyLine(pack, card.party_role)}</span>
      <span className="av-cases-row__step">
        {index >= 0 && card.stage && (
          <>
            <StepRing step={stepLabel(card.stage)} number={index + 1} total={steps.length} />
            <span className="av-muted">{t("cases.step_of", { number: index + 1, total: steps.length })}</span>
          </>
        )}
      </span>
      <span>{card.due_date ? formatDue(card.due_date, locale ?? pack?.locale) : ""}</span>
    </Link>
  );
}

const CARDS = [
  { key: "papers", d: "M6 3h9l4 4v14H6zM14 3v5h5" },
  { key: "safe", d: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" },
  { key: "team", d: "M16 11a3 3 0 1 0-6 0 3 3 0 0 0 6 0zM5 20c0-3 3-5 7-5s7 2 7 5" },
] as const;

/** S03e: faded placeholder rows, one clear way to start, and three short reassurances. */
function CasesEmpty() {
  return (
    <>
      <section className="av-card av-cases-empty">
        <div aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="av-cases-empty__ghost" />
          ))}
        </div>
        <div className="av-cases-empty__message">
          <IconTile tone="mint">
            <Icon d="M6 3h9l4 4v14H6zM14 3v5h5" size={26} />
          </IconTile>
          <h2>{t("cases.empty.title")}</h2>
          <p className="av-muted">{t("cases.empty.body")}</p>
          {hasScreen("new_case") && (
            <LinkButton to={screenPath("new_case")!}>
              <Icon d="M12 5v14M5 12h14" />
              {t("cases.empty.start")}
            </LinkButton>
          )}
        </div>
      </section>
      <div className="av-cases-empty__cards">
        {CARDS.map((c) => (
          <section key={c.key} className="av-card av-stack">
            <IconTile tone="mint">
              <Icon d={c.d} size={26} />
            </IconTile>
            <h3>{t(`cases.empty.${c.key}.title`)}</h3>
            <p className="av-muted">{t(`cases.empty.${c.key}.body`)}</p>
          </section>
        ))}
      </div>
    </>
  );
}
